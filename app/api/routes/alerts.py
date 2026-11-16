from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import case, func, select

from app import schemas
from app.api.deps import DB, Paging, get_or_404
from app.models import Alert, CheckResult, CheckRun, DataSource, ValidationRule

router = APIRouter(tags=["alerts & dashboard"])


@router.get("/alerts", response_model=schemas.Page[schemas.AlertOut])
def list_alerts(
    db: DB,
    page: Paging,
    status_filter: str | None = Query(default=None, alias="status"),
    severity: str | None = None,
):
    stmt = select(Alert)
    count = select(func.count()).select_from(Alert)
    for col, value in ((Alert.status, status_filter), (Alert.severity, severity)):
        if value:
            stmt, count = stmt.where(col == value), count.where(col == value)
    items = db.scalars(stmt.order_by(Alert.id.desc()).limit(page.limit).offset(page.offset)).all()
    return {"items": items, "total": db.scalar(count), "limit": page.limit, "offset": page.offset}


def _transition(db, alert_id: int, allowed_from: tuple[str, ...], to: str) -> Alert:
    alert = get_or_404(db, Alert, alert_id)
    if alert.status not in allowed_from:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Alert is already {alert.status}")
    alert.status = to
    now = datetime.now(UTC)
    if to == "acknowledged":
        alert.acknowledged_at = now
    if to == "resolved":
        alert.resolved_at = now
    db.commit()
    db.refresh(alert)
    return alert


@router.post("/alerts/{alert_id}/acknowledge", response_model=schemas.AlertOut)
def acknowledge_alert(alert_id: int, db: DB):
    return _transition(db, alert_id, ("open",), "acknowledged")


@router.post("/alerts/{alert_id}/resolve", response_model=schemas.AlertOut)
def resolve_alert(alert_id: int, db: DB):
    return _transition(db, alert_id, ("open", "acknowledged"), "resolved")


@router.get("/dashboard/summary", response_model=schemas.SummaryOut)
def summary(db: DB):
    """Aggregates computed in SQL (GROUP BY), not by loading rows into Python."""
    since = datetime.now(UTC) - timedelta(hours=24)
    active = Alert.status.in_(("open", "acknowledged"))

    results_24h, passed_24h = db.execute(
        select(func.count(), func.sum(case((CheckResult.status == "passed", 1), else_=0))).where(
            CheckResult.created_at >= since
        )
    ).one()

    open_by_source = dict(
        db.execute(
            select(ValidationRule.data_source_id, func.count(Alert.id))
            .join(Alert, Alert.rule_id == ValidationRule.id)
            .where(active)
            .group_by(ValidationRule.data_source_id)
        ).all()
    )

    # Latest run per source via a "max id per group" subquery.
    latest = (
        select(CheckRun.data_source_id, func.max(CheckRun.id).label("run_id"))
        .group_by(CheckRun.data_source_id)
        .subquery()
    )
    rows = db.execute(
        select(DataSource, CheckRun)
        .outerjoin(latest, latest.c.data_source_id == DataSource.id)
        .outerjoin(CheckRun, CheckRun.id == latest.c.run_id)
        .order_by(DataSource.id)
    ).all()

    sources = []
    for source, run in rows:
        rate = None
        if run and run.total_rules:
            rate = round(run.passed_rules / run.total_rules, 4)
        sources.append(
            {
                "data_source_id": source.id,
                "name": source.name,
                "last_run_id": run.id if run else None,
                "last_run_status": run.status if run else None,
                "last_run_at": (run.finished_at or run.created_at) if run else None,
                "pass_rate": rate,
                "open_alerts": open_by_source.get(source.id, 0),
            }
        )

    return {
        "data_sources": len(rows),
        "active_rules": db.scalar(
            select(func.count()).select_from(ValidationRule).where(ValidationRule.is_active)
        ),
        "open_alerts": db.scalar(select(func.count()).select_from(Alert).where(active)),
        "critical_open_alerts": db.scalar(
            select(func.count()).select_from(Alert).where(active, Alert.severity == "critical")
        ),
        "results_last_24h": results_24h,
        "pass_rate_last_24h": round((passed_24h or 0) / results_24h, 4) if results_24h else None,
        "sources": sources,
    }
