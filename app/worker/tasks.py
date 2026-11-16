"""Celery tasks. Each task opens its own DB session: tasks run in worker processes,
outside any FastAPI request."""

from __future__ import annotations

import logging

import httpx

from app.database import SessionLocal
from app.engine import runner
from app.models import Alert
from app.observability import NOTIFICATIONS, log_extra
from app.services import alerting, scheduler
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="dq.run_checks")
def run_checks(run_id: int) -> dict:
    db = SessionLocal()
    try:
        notifications = runner.execute_run(db, run_id)
    except Exception as exc:
        logger.exception("run failed", extra=log_extra(run_id=run_id))
        runner.mark_run_failed(db, run_id, f"{type(exc).__name__}: {exc}")
        raise
    finally:
        db.close()
    for n in notifications:
        try:
            send_alert_notification.delay(n.alert_id, n.event)
        except Exception:  # a notification problem must never fail the run itself
            logger.exception("could not enqueue notification", extra=log_extra(alert_id=n.alert_id))
    return {"run_id": run_id, "notifications": len(notifications)}


@celery_app.task(
    name="dq.send_alert_notification",
    autoretry_for=(httpx.HTTPError,),
    retry_backoff=True,  # 1s, 2s, 4s, ... between attempts
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=5,
)
def send_alert_notification(alert_id: int, event: str) -> bool:
    db = SessionLocal()
    try:
        alert = db.get(Alert, alert_id)
        if alert is None:
            return False
        payload = alerting.format_notification(alert, alert.rule, event)
    finally:
        db.close()
    try:
        return alerting.send_webhook(payload)
    except httpx.HTTPError:
        NOTIFICATIONS.labels("failed").inc()
        raise


@celery_app.task(name="dq.dispatch_scheduled_runs")
def dispatch_scheduled_runs() -> list[int]:
    db = SessionLocal()
    try:
        run_ids = scheduler.dispatch_due_runs(db)
    finally:
        db.close()
    for run_id in run_ids:
        run_checks.delay(run_id)
    if run_ids:
        logger.info("dispatched scheduled runs", extra=log_extra(run_ids=run_ids))
    return run_ids
