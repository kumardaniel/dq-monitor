"""Inspect and replay the dead-letter queue (dq.dead).

    python scripts/dlq.py list              # show dead-lettered tasks (messages stay put)
    python scripts/dlq.py replay            # send them back to dq.tasks with fresh retries
    python scripts/dlq.py replay --limit 1

Replaying a run puts its check_runs row back to "queued" first; the runner only
executes queued runs (idempotency), so without that the replay would be a no-op.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kombu import Connection  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import CheckRun  # noqa: E402
from app.worker.celery_app import (  # noqa: E402
    DEAD_LETTER_QUEUE,
    TASK_EXCHANGE,
    TASK_QUEUE,
)


def describe(message) -> str:
    headers = message.headers or {}
    args = message.decode()[0] if message.body else []
    reason = (headers.get("x-death") or [{}])[0].get("reason", "?")
    return f"{headers.get('task', '?')}{tuple(args)}  id={headers.get('id')}  reason={reason}"


def list_messages(conn: Connection, limit: int) -> None:
    queue = DEAD_LETTER_QUEUE.bind(conn.default_channel)
    queue.declare()
    seen = []
    for _ in range(limit):
        message = queue.get(no_ack=False)
        if message is None:
            break
        seen.append(message)
        print(describe(message))
    for message in seen:  # put everything back untouched
        message.requeue()
    print(f"{len(seen)} message(s) in {DEAD_LETTER_QUEUE.name}")


def requeue_run_row(run_id: int) -> None:
    with SessionLocal() as db:
        run = db.get(CheckRun, run_id)
        if run is not None:
            run.status, run.error_message, run.started_at, run.finished_at = (
                "queued",
                None,
                None,
                None,
            )
            db.commit()


def replay(conn: Connection, limit: int) -> None:
    channel = conn.default_channel
    queue = DEAD_LETTER_QUEUE.bind(channel)
    queue.declare()
    TASK_QUEUE.bind(channel).declare()
    producer = conn.Producer(channel)
    count = 0
    for _ in range(limit):
        message = queue.get(no_ack=False)
        if message is None:
            break
        headers = dict(message.headers or {})
        if headers.get("task") == "dq.run_checks":
            requeue_run_row(int(message.decode()[0][0]))
        headers["retries"] = 0  # a fresh set of retries
        headers.pop("x-death", None)
        producer.publish(
            message.body,
            exchange=TASK_EXCHANGE,
            routing_key="dq.tasks",
            headers=headers,
            content_type=message.content_type,
            content_encoding=message.content_encoding,
            delivery_mode=2,
        )
        message.ack()  # only after the copy is safely published
        print(f"replayed {describe(message)}")
        count += 1
    print(f"{count} message(s) replayed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["list", "replay"])
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    with Connection(get_settings().celery_broker_url) as conn:
        (list_messages if args.command == "list" else replay)(conn, args.limit)


if __name__ == "__main__":
    main()
