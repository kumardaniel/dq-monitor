"""Queue behaviour: retries, dead-lettering configuration, idempotency, broker outages."""

from sqlalchemy.exc import OperationalError

from app.config import get_settings
from app.engine import runner
from app.models import CheckRun
from app.worker import tasks
from app.worker.celery_app import DEAD_LETTER_QUEUE, TASK_QUEUE, celery_app


def test_queue_is_wired_to_dead_letter_exchange():
    args = TASK_QUEUE.queue_arguments
    assert args["x-dead-letter-exchange"] == DEAD_LETTER_QUEUE.exchange.name
    assert args["x-dead-letter-routing-key"] == DEAD_LETTER_QUEUE.routing_key
    assert TASK_QUEUE.durable and DEAD_LETTER_QUEUE.durable


def test_failed_tasks_are_rejected_not_acked():
    conf = celery_app.conf
    assert conf.task_acks_late is True  # redelivered if a worker dies mid-task
    assert conf.task_reject_on_worker_lost is True
    assert conf.task_acks_on_failure_or_timeout is False  # final failure -> dead-letter
    assert conf.task_default_queue == TASK_QUEUE.name


def deliver_until_settled(task, args, monkeypatch):
    """Run a task the way a worker would. Celery's eager mode re-runs each retry inline
    (with retries + 1), so one apply() covers every attempt. Propagation is switched off
    so the final outcome comes back as a result instead of an exception."""
    monkeypatch.setitem(celery_app.conf, "task_eager_propagates", False)
    return task.apply(args=args)


def _queued_run(client, source) -> int:
    from app.database import SessionLocal
    from app.services.scheduler import create_run

    with SessionLocal() as db:
        from app.models import DataSource

        run = create_run(db, db.get(DataSource, source["id"]), trigger="manual")
        db.commit()
        return run.id


def test_infrastructure_failure_retries_then_marks_run_failed(client, source, monkeypatch, db):
    run_id = _queued_run(client, source)
    calls = []
    real_execute = runner.execute_run

    def flaky(session, rid):
        calls.append(rid)
        real_execute_state = session.get(CheckRun, rid)
        real_execute_state.status = "running"  # simulate getting partway in
        session.commit()
        raise OperationalError("SELECT 1", {}, Exception("metadata DB unavailable"))

    monkeypatch.setattr(runner, "execute_run", flaky)
    result = deliver_until_settled(tasks.run_checks, [run_id], monkeypatch)

    # 1 attempt + max_retries retries, each starting from a re-queued run; then the task
    # fails for good, which on RabbitMQ means the message is rejected into dq.dead.
    assert result.state == "FAILURE"
    assert isinstance(result.result, OperationalError)
    assert len(calls) == get_settings().run_task_max_retries + 1
    db.expire_all()
    run = db.get(CheckRun, run_id)
    assert run.status == "failed"
    assert "metadata DB unavailable" in run.error_message
    monkeypatch.setattr(runner, "execute_run", real_execute)


def test_transient_failure_recovers_on_retry(client, source, make_rule, monkeypatch, db):
    make_rule(rule_type="not_null", column_name="order_id")
    run_id = _queued_run(client, source)
    real_execute = runner.execute_run
    attempts = {"n": 0}

    def fails_once(session, rid):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise OperationalError("SELECT 1", {}, Exception("blip"))
        return real_execute(session, rid)

    monkeypatch.setattr(runner, "execute_run", fails_once)
    result = deliver_until_settled(tasks.run_checks, [run_id], monkeypatch)
    assert result.state == "SUCCESS"
    db.expire_all()
    assert attempts["n"] == 2
    assert db.get(CheckRun, run_id).status == "completed"


def test_duplicate_delivery_is_a_no_op(client, source, make_rule, db):
    make_rule(rule_type="not_null", column_name="order_id")
    run_id = client.post(f"/api/v1/data-sources/{source['id']}/runs").json()["id"]
    before = client.get(f"/api/v1/runs/{run_id}").json()
    tasks.run_checks.apply(args=[run_id], throw=True)  # same message delivered again
    after = client.get(f"/api/v1/runs/{run_id}").json()
    assert len(after["results"]) == len(before["results"]) == 1


def test_broker_down_fails_run_instead_of_leaving_it_queued(client, source, monkeypatch):
    monkeypatch.setattr(get_settings(), "celery_task_always_eager", False)

    def broker_down(*_a, **_k):
        raise ConnectionRefusedError("rabbitmq unreachable")

    monkeypatch.setattr(tasks.run_checks, "delay", broker_down)
    resp = client.post(f"/api/v1/data-sources/{source['id']}/runs")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "service_unavailable"

    run = client.get("/api/v1/runs").json()["items"][0]
    assert run["status"] == "failed"
    assert "Could not enqueue" in run["error_message"]
    # Not stuck: a new run can be triggered once the broker is back.
    monkeypatch.setattr(get_settings(), "celery_task_always_eager", True)
    monkeypatch.undo()
    assert client.post(f"/api/v1/data-sources/{source['id']}/runs").status_code == 202
