"""Executes a CheckRun: runs every active rule of a data source and records results.

Design notes
- Idempotent: only a run in status "queued" is executed. If the broker delivers the
  same task twice (Celery's at-least-once delivery), the second delivery is a no-op.
- Isolated failures: one broken rule is recorded as status "error" and the run
  carries on with the remaining rules.
- Each rule runs on its own connection/transaction, so a failed statement cannot
  poison the rules after it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import cache
from app.config import get_settings
from app.engine.checks import CheckContext, CheckOutcome, get_check
from app.engine.connectors import source_connection
from app.models import CheckResult, CheckRun, DataSource, ValidationRule
from app.observability import CHECK_DURATION, CHECK_RESULTS, RUNS, log_extra
from app.services import alerting

logger = logging.getLogger(__name__)


@dataclass
class PendingNotification:
    alert_id: int
    event: str


def execute_rule(url: str, rule: ValidationRule) -> tuple[CheckOutcome, float]:
    settings = get_settings()
    ctx = CheckContext(
        table_name=rule.table_name,
        column_name=rule.column_name,
        config=rule.rule_config or {},
        threshold=rule.threshold,
        max_samples=settings.max_failing_samples,
    )
    start = time.perf_counter()
    try:
        with source_connection(url) as conn:
            outcome = get_check(rule.rule_type).run(conn, ctx)
    except Exception as exc:  # any DB/config error becomes an "error" result
        logger.warning("check errored", extra=log_extra(rule_id=rule.id, error=str(exc)[:500]))
        outcome = CheckOutcome("error", f"{type(exc).__name__}: {str(exc)[:500]}")
    elapsed = time.perf_counter() - start
    CHECK_DURATION.labels(rule.rule_type).observe(elapsed)
    CHECK_RESULTS.labels(rule.rule_type, outcome.status).inc()
    return outcome, elapsed * 1000


def execute_run(db: Session, run_id: int) -> list[PendingNotification]:
    """Run all checks for a run. Returns notifications to send after commit."""
    run = db.get(CheckRun, run_id)
    if run is None or run.status != "queued":
        logger.info("skipping run", extra=log_extra(run_id=run_id))
        return []

    run.status = "running"
    run.started_at = datetime.now(UTC)
    db.commit()
    cache.invalidate_source(run.data_source_id)

    source: DataSource = run.data_source
    rules = db.scalars(
        select(ValidationRule)
        .where(ValidationRule.data_source_id == source.id, ValidationRule.is_active.is_(True))
        .order_by(ValidationRule.id)
    ).all()

    notifications: list[PendingNotification] = []
    counts = {"passed": 0, "failed": 0, "error": 0}
    url = source.connection_config.get("url", "")

    for rule in rules:
        outcome, duration_ms = execute_rule(url, rule)
        result = CheckResult(
            run_id=run.id,
            rule_id=rule.id,
            status=outcome.status,
            total_rows=outcome.total_rows,
            failing_rows=outcome.failing_rows,
            observed_value=outcome.observed_value,
            message=outcome.message,
            sample_failures=outcome.sample_failures,
            duration_ms=round(duration_ms, 2),
        )
        db.add(result)
        db.flush()  # assigns result.id for the alert to reference
        counts[outcome.status] += 1
        if due := alerting.process_result(db, rule, result):
            alert, event = due
            notifications.append(PendingNotification(alert.id, event))

    run.total_rules = len(rules)
    run.passed_rules = counts["passed"]
    run.failed_rules = counts["failed"]
    run.error_rules = counts["error"]
    run.finished_at = datetime.now(UTC)
    run.status = "completed"
    db.commit()
    # Invalidate AFTER commit: invalidating first would let a concurrent read re-cache
    # the old state before the new state is visible.
    cache.invalidate_source(source.id)

    RUNS.labels(run.status, run.trigger).inc()
    logger.info(
        "run finished",
        extra=log_extra(run_id=run.id, source=source.name, **counts),
    )
    return notifications


def mark_run_failed(db: Session, run_id: int, error: str) -> None:
    db.rollback()
    run = db.get(CheckRun, run_id)
    if run is None:
        return
    run.status = "failed"
    run.error_message = error[:2000]
    run.finished_at = datetime.now(UTC)
    db.commit()
    cache.invalidate_source(run.data_source_id)
    RUNS.labels("failed", run.trigger).inc()


def requeue_run(db: Session, run_id: int) -> None:
    """Put a run back to "queued" so a retried task is allowed to execute it again."""
    db.rollback()
    run = db.get(CheckRun, run_id)
    if run is not None and run.status == "running":
        run.status = "queued"
        run.started_at = None
        db.commit()
