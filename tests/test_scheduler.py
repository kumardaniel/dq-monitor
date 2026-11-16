from datetime import UTC, datetime, timedelta

from app.models import CheckRun, DataSource
from app.services.scheduler import dispatch_due_runs
from app.worker.tasks import dispatch_scheduled_runs


def _source(db, cron="*/5 * * * *", created=None, **kw) -> DataSource:
    s = DataSource(
        name=kw.pop("name", "s"),
        source_type="sqlite",
        connection_config={"url": "sqlite://"},
        schedule_cron=cron,
        **kw,
    )
    db.add(s)
    db.commit()
    s.created_at = created or datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    db.commit()
    return s


def test_due_source_gets_a_run(db):
    s = _source(db)
    ids = dispatch_due_runs(db, now=datetime(2026, 1, 1, 12, 6, tzinfo=UTC))
    assert len(ids) == 1
    run = db.get(CheckRun, ids[0])
    assert run.trigger == "scheduled" and run.data_source_id == s.id


def test_not_yet_due_source_is_skipped(db):
    _source(db)
    assert dispatch_due_runs(db, now=datetime(2026, 1, 1, 12, 3, tzinfo=UTC)) == []


def test_manual_only_and_inactive_sources_are_skipped(db):
    _source(db, cron=None, name="manual")
    _source(db, name="paused", is_active=False)
    assert dispatch_due_runs(db, now=datetime(2026, 1, 2, tzinfo=UTC)) == []


def test_no_pile_up_while_a_run_is_in_flight(db):
    _source(db)
    t = datetime(2026, 1, 1, 12, 6, tzinfo=UTC)
    assert len(dispatch_due_runs(db, now=t)) == 1
    # Next tick is due again, but the first run is still "queued".
    assert dispatch_due_runs(db, now=t + timedelta(minutes=5)) == []


def test_beat_task_dispatches_and_executes(db, warehouse_url):
    s = _source(db, created=datetime.now(UTC) - timedelta(hours=1))
    s.connection_config = {"url": warehouse_url}
    db.commit()
    ids = dispatch_scheduled_runs()  # eager: also runs the checks
    assert len(ids) == 1
    db.expire_all()
    assert db.get(CheckRun, ids[0]).status == "completed"
