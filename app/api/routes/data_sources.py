import logging

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import cache, schemas
from app.api.deps import DB, CurrentUser, Paging, get_or_404
from app.config import get_settings
from app.engine.connectors import ConnectionConfigError, test_connection, validate_connection
from app.engine.runner import mark_run_failed
from app.models import Alert, CheckRun, DataSource, ValidationRule
from app.services.scheduler import create_run, has_run_in_flight
from app.worker.tasks import run_checks

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/data-sources", tags=["data sources"])


@router.post("", response_model=schemas.DataSourceOut, status_code=status.HTTP_201_CREATED)
def create_data_source(payload: schemas.DataSourceCreate, db: DB, user: CurrentUser):
    source = DataSource(**payload.model_dump(), owner_id=user.id)
    db.add(source)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Data source '{payload.name}' exists"
        ) from None
    db.refresh(source)
    cache.invalidate_source(source.id)
    return source


@router.get("", response_model=schemas.Page[schemas.DataSourceOut])
def list_data_sources(db: DB, page: Paging):
    total = db.scalar(select(func.count()).select_from(DataSource))
    items = db.scalars(
        select(DataSource).order_by(DataSource.id).limit(page.limit).offset(page.offset)
    ).all()
    return {"items": items, "total": total, "limit": page.limit, "offset": page.offset}


@router.get("/{source_id}", response_model=schemas.DataSourceOut)
def get_data_source(source_id: int, db: DB):
    return get_or_404(db, DataSource, source_id)


@router.patch("/{source_id}", response_model=schemas.DataSourceOut)
def update_data_source(source_id: int, payload: schemas.DataSourceUpdate, db: DB):
    source = get_or_404(db, DataSource, source_id)
    changes = payload.model_dump(exclude_unset=True)
    if "connection_config" in changes:
        try:
            validate_connection(source.source_type, changes["connection_config"])
        except ConnectionConfigError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    for key, value in changes.items():
        setattr(source, key, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Name already in use") from None
    db.refresh(source)
    cache.invalidate_source(source.id)
    return source


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_data_source(source_id: int, db: DB):
    db.delete(get_or_404(db, DataSource, source_id))
    db.commit()
    cache.invalidate_source(source_id)


@router.post("/{source_id}/test-connection")
def test_data_source_connection(source_id: int, db: DB):
    source = get_or_404(db, DataSource, source_id)
    try:
        test_connection(source.connection_config["url"])
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    return {"ok": True}


@router.post(
    "/{source_id}/runs",
    response_model=schemas.RunOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger a check run (executes asynchronously on a worker)",
)
def trigger_run(source_id: int, db: DB):
    source = get_or_404(db, DataSource, source_id)
    if has_run_in_flight(db, source.id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A run is already queued or running"
        ) from None
    run = create_run(db, source, trigger="manual")
    db.commit()  # commit BEFORE enqueueing so the worker can see the row
    cache.invalidate_source(source.id)
    try:
        run_checks.delay(run.id)
    except Exception as exc:
        if get_settings().celery_task_always_eager:
            # Eager (local) mode runs the task inline; its failure is already recorded
            # on the run row, so report the row rather than a 500.
            logger.exception("run task raised")
        else:
            # Broker unreachable: don't leave a "queued" run that nothing will ever pick up.
            mark_run_failed(db, run.id, f"Could not enqueue run: {exc}")
            cache.invalidate_source(source.id)
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "Task queue unavailable; run not started"
            ) from exc
    db.refresh(run)
    return run


@router.get("/{source_id}/runs", response_model=schemas.Page[schemas.RunOut])
def list_source_runs(source_id: int, db: DB, page: Paging):
    get_or_404(db, DataSource, source_id)
    where = CheckRun.data_source_id == source_id
    total = db.scalar(select(func.count()).select_from(CheckRun).where(where))
    items = db.scalars(
        select(CheckRun)
        .where(where)
        .order_by(CheckRun.id.desc())
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return {"items": items, "total": total, "limit": page.limit, "offset": page.offset}


def compute_source_status(db: Session, source: DataSource) -> dict:
    last_run = db.scalars(
        select(CheckRun)
        .where(CheckRun.data_source_id == source.id)
        .order_by(CheckRun.id.desc())
        .limit(1)
    ).first()
    alert_counts = dict(
        db.execute(
            select(Alert.severity, func.count())
            .join(ValidationRule, Alert.rule_id == ValidationRule.id)
            .where(
                ValidationRule.data_source_id == source.id,
                Alert.status.in_(("open", "acknowledged")),
            )
            .group_by(Alert.severity)
        ).all()
    )
    open_alerts = sum(alert_counts.values())
    if last_run is None or last_run.status not in ("completed", "failed"):
        health = "unknown" if open_alerts == 0 else "failing"
    else:
        healthy = last_run.status == "completed" and not (
            last_run.failed_rules or last_run.error_rules
        )
        health = "healthy" if healthy and open_alerts == 0 else "failing"
    return {
        "data_source_id": source.id,
        "name": source.name,
        "health": health,
        "last_run": schemas.RunOut.model_validate(last_run).model_dump(mode="json")
        if last_run
        else None,
        "open_alerts": open_alerts,
        "critical_open_alerts": alert_counts.get("critical", 0),
    }


@router.get(
    "/{source_id}/status",
    response_model=schemas.SourceStatusOut,
    summary="Latest health of a source (cached in Redis; see X-Cache header)",
)
def source_status(source_id: int, db: DB, response: Response):
    source = get_or_404(db, DataSource, source_id)
    value, hit = cache.get_or_set(
        cache.source_status_key(source_id), lambda: compute_source_status(db, source)
    )
    response.headers["X-Cache"] = "HIT" if hit else "MISS"
    return value
