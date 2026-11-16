from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app import schemas
from app.api.deps import DB, Paging, get_or_404
from app.models import CheckRun

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("", response_model=schemas.Page[schemas.RunOut])
def list_runs(db: DB, page: Paging, status: str | None = Query(default=None)):
    stmt = select(CheckRun)
    count = select(func.count()).select_from(CheckRun)
    if status:
        stmt, count = stmt.where(CheckRun.status == status), count.where(CheckRun.status == status)
    items = db.scalars(
        stmt.order_by(CheckRun.id.desc()).limit(page.limit).offset(page.offset)
    ).all()
    return {"items": items, "total": db.scalar(count), "limit": page.limit, "offset": page.offset}


@router.get("/{run_id}", response_model=schemas.RunDetailOut)
def get_run(run_id: int, db: DB):
    # selectinload fetches all results in one extra query instead of one per result (N+1).
    run = db.scalars(
        select(CheckRun).options(selectinload(CheckRun.results)).where(CheckRun.id == run_id)
    ).first()
    return run or get_or_404(db, CheckRun, run_id)
