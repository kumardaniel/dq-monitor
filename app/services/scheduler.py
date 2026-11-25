"""Finds data sources whose cron schedule is due and creates queued runs for them.

Celery beat calls this every `scheduler_tick_seconds`. If several beat/worker
processes ever ran it at once, `SELECT ... FOR UPDATE SKIP LOCKED` (PostgreSQL) makes
each source row lockable by only one of them, so a source is never dispatched twice.
On SQLite the locking clause is simply ignored.
"""

from __future__ import annotations

from datetime import UTC, datetime

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import cache
from app.models import CheckRun, DataSource

IN_FLIGHT = ("queued", "running")


def is_valid_cron(expr: str) -> bool:
    return croniter.is_valid(expr)


def next_due(source: DataSource) -> datetime:
    base = source.last_scheduled_at or source.created_at
    if base.tzinfo is None:
        base = base.replace(tzinfo=UTC)
    return croniter(source.schedule_cron, base).get_next(datetime)


def create_run(db: Session, source: DataSource, trigger: str) -> CheckRun:
    run = CheckRun(data_source_id=source.id, status="queued", trigger=trigger)
    db.add(run)
    db.flush()
    return run


def has_run_in_flight(db: Session, source_id: int) -> bool:
    return (
        db.scalar(
            select(CheckRun.id)
            .where(CheckRun.data_source_id == source_id, CheckRun.status.in_(IN_FLIGHT))
            .limit(1)
        )
        is not None
    )


def dispatch_due_runs(db: Session, now: datetime | None = None) -> list[int]:
    """Create runs for every due source. Returns the new run ids (caller enqueues them)."""
    now = now or datetime.now(UTC)
    sources = db.scalars(
        select(DataSource)
        .where(DataSource.is_active.is_(True), DataSource.schedule_cron.is_not(None))
        .with_for_update(skip_locked=True)
    ).all()

    run_ids: list[int] = []
    for source in sources:
        if next_due(source) > now:
            continue
        source.last_scheduled_at = now
        # Don't pile up runs if the previous one is still going (slow source/worker).
        if has_run_in_flight(db, source.id):
            continue
        run_ids.append(create_run(db, source, trigger="scheduled").id)
    db.commit()
    for run_id in run_ids:
        cache.invalidate_source(db.get(CheckRun, run_id).data_source_id)
    return run_ids
