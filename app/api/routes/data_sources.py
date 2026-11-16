import logging

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import schemas
from app.api.deps import DB, Paging, get_or_404
from app.engine.connectors import ConnectionConfigError, test_connection, validate_connection
from app.models import CheckRun, DataSource
from app.services.scheduler import create_run, has_run_in_flight
from app.worker.tasks import run_checks

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/data-sources", tags=["data sources"])


@router.post("", response_model=schemas.DataSourceOut, status_code=status.HTTP_201_CREATED)
def create_data_source(payload: schemas.DataSourceCreate, db: DB):
    source = DataSource(**payload.model_dump())
    db.add(source)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Data source '{payload.name}' exists"
        ) from None
    db.refresh(source)
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
    return source


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_data_source(source_id: int, db: DB):
    db.delete(get_or_404(db, DataSource, source_id))
    db.commit()


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
    try:
        run_checks.delay(run.id)
    except Exception:
        # In eager (local) mode the run executes inline; its failure is already
        # recorded on the run row, so report the row rather than a 500.
        logger.exception("run task raised")
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
