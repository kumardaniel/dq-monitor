"""Celery application on RabbitMQ, with a dead-letter queue.

Start a worker:  celery -A app.worker.celery_app worker -Q dq.tasks --loglevel=INFO
Start beat:      celery -A app.worker.celery_app beat --loglevel=INFO

Message flow:

    producer --> exchange "dq" --> queue "dq.tasks" --> worker
                                       |
                    rejected (failed   |  x-dead-letter-exchange
                    after all retries) v
                 exchange "dq.dlx" --> queue "dq.dead"   (kept for inspection/replay)

A task that keeps failing is never silently dropped: once its retries are used up the
worker *rejects* the message, and RabbitMQ re-routes it to dq.dead.
See scripts/dlq.py to list or replay dead-lettered messages.
"""

from celery import Celery
from celery.signals import setup_logging, worker_ready
from kombu import Exchange, Queue

from app.config import get_settings
from app.observability import configure_logging

settings = get_settings()

TASK_EXCHANGE = Exchange("dq", type="direct", durable=True)
DEAD_LETTER_EXCHANGE = Exchange("dq.dlx", type="direct", durable=True)
TASK_QUEUE = Queue(
    "dq.tasks",
    TASK_EXCHANGE,
    routing_key="dq.tasks",
    durable=True,
    queue_arguments={
        "x-dead-letter-exchange": DEAD_LETTER_EXCHANGE.name,
        "x-dead-letter-routing-key": "dq.dead",
    },
)
DEAD_LETTER_QUEUE = Queue("dq.dead", DEAD_LETTER_EXCHANGE, routing_key="dq.dead", durable=True)

celery_app = Celery("dq_monitor", broker=settings.celery_broker_url, include=["app.worker.tasks"])

celery_app.conf.update(
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=True,
    task_queues=(TASK_QUEUE,),
    task_default_queue=TASK_QUEUE.name,
    task_default_exchange=TASK_EXCHANGE.name,
    task_default_routing_key="dq.tasks",
    # acks_late + reject_on_worker_lost: a message is acknowledged only after the task
    # finishes, so if a worker crashes mid-run RabbitMQ redelivers it. Runs are idempotent
    # (engine/runner.py), which makes redelivery safe.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # False = a task that finally fails is *rejected* (not acked), so RabbitMQ moves it
    # to the dead-letter queue instead of discarding it.
    task_acks_on_failure_or_timeout=False,
    worker_prefetch_multiplier=1,
    # Results live in our own DB (check_runs/check_results), so no result backend.
    task_ignore_result=True,
    task_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    broker_connection_retry_on_startup=True,
    worker_redirect_stdouts=False,
    beat_schedule={
        "dispatch-scheduled-runs": {
            "task": "dq.dispatch_scheduled_runs",
            "schedule": float(settings.scheduler_tick_seconds),
        }
    },
)


def declare_dead_letter_queue() -> None:
    """The worker only consumes dq.tasks, so declare dq.dead explicitly: dead-lettered
    messages are dropped by RabbitMQ if their target queue doesn't exist."""
    with celery_app.connection_for_write() as conn:
        DEAD_LETTER_QUEUE.bind(conn.default_channel).declare()


@setup_logging.connect
def _setup_logging(**_kwargs) -> None:
    # Connecting this signal stops Celery from installing its own log handlers, so the
    # worker emits the same JSON log lines as the API.
    configure_logging(settings.log_level)


@worker_ready.connect
def _on_worker_ready(**_kwargs) -> None:
    declare_dead_letter_queue()
    # Expose the worker's own Prometheus metrics (checks run here, not in the API).
    from prometheus_client import start_http_server

    start_http_server(9100)
