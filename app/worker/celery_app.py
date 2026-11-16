"""Celery application: background workers + the beat scheduler.

Start a worker:  celery -A app.worker.celery_app worker --loglevel=INFO
Start beat:      celery -A app.worker.celery_app beat --loglevel=INFO
"""

from celery import Celery
from celery.signals import worker_ready

from app.config import get_settings
from app.observability import configure_logging

settings = get_settings()

celery_app = Celery(
    "dq_monitor",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=True,
    # acks_late + reject_on_worker_lost: a task is acknowledged only after it finishes,
    # so if a worker crashes mid-run the broker redelivers it. Runs are idempotent
    # (see engine/runner.py), which makes redelivery safe.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_expires=3600,
    timezone="UTC",
    beat_schedule={
        "dispatch-scheduled-runs": {
            "task": "dq.dispatch_scheduled_runs",
            "schedule": float(settings.scheduler_tick_seconds),
        }
    },
)


@worker_ready.connect
def _on_worker_ready(**_kwargs) -> None:
    configure_logging(settings.log_level)
    # Expose the worker's own Prometheus metrics (checks run here, not in the API).
    from prometheus_client import start_http_server

    start_http_server(9100)
