"""Alert lifecycle: open on failure, deduplicate repeats, auto-resolve on recovery.

Without deduplication a rule that fails every 5 minutes would page someone every
5 minutes. Instead there is at most one *active* (open or acknowledged) alert per rule;
repeat failures bump its `occurrences` counter and only the first one notifies.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Alert, CheckResult, ValidationRule
from app.observability import ALERTS_OPENED, NOTIFICATIONS, log_extra

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("open", "acknowledged")


def active_alert_for_rule(db: Session, rule_id: int) -> Alert | None:
    return db.scalars(
        select(Alert).where(Alert.rule_id == rule_id, Alert.status.in_(ACTIVE_STATUSES))
    ).first()


def process_result(
    db: Session, rule: ValidationRule, result: CheckResult
) -> tuple[Alert, str] | None:
    """Update alert state for one result. Returns (alert, event) if a notification is due.

    event is "opened" or "resolved". The caller sends notifications *after* committing,
    so a worker never notifies about a row that might still be rolled back.
    """
    now = datetime.now(UTC)
    existing = active_alert_for_rule(db, rule.id)

    if result.status in ("failed", "error"):
        if existing:
            existing.occurrences += 1
            existing.last_seen_at = now
            existing.last_result_id = result.id
            existing.message = result.message
            return None
        alert = Alert(
            rule_id=rule.id,
            last_result_id=result.id,
            severity=rule.severity,
            status="open",
            message=result.message,
            last_seen_at=now,
        )
        db.add(alert)
        db.flush()
        ALERTS_OPENED.labels(rule.severity).inc()
        return alert, "opened"

    if existing:  # rule passed again: close the incident
        existing.status = "resolved"
        existing.resolved_at = now
        return existing, "resolved"
    return None


def format_notification(alert: Alert, rule: ValidationRule, event: str) -> dict:
    icon = ":rotating_light:" if event == "opened" else ":white_check_mark:"
    title = f"{icon} [{alert.severity.upper()}] DQ alert {event}: {rule.name}"
    return {
        "text": f"{title}\n{alert.message}",
        "alert_id": alert.id,
        "rule_id": rule.id,
        "data_source_id": rule.data_source_id,
        "event": event,
        "severity": alert.severity,
    }


def send_webhook(payload: dict) -> bool:
    """POST the payload to the configured webhook. Raises on HTTP errors so the
    Celery task can retry; returns False when no webhook is configured."""
    settings = get_settings()
    if not settings.alert_webhook_url:
        NOTIFICATIONS.labels("skipped").inc()
        return False
    resp = httpx.post(
        settings.alert_webhook_url,
        json=payload,
        timeout=settings.alert_webhook_timeout_seconds,
    )
    resp.raise_for_status()
    NOTIFICATIONS.labels("sent").inc()
    logger.info("alert notification sent", extra=log_extra(alert_id=payload.get("alert_id")))
    return True
