from fastapi import APIRouter, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.api.deps import DB

router = APIRouter(tags=["ops"])


@router.get("/health", summary="Liveness: the process is up")
def health():
    return {"status": "ok"}


@router.get("/ready", summary="Readiness: dependencies (database) are reachable")
def ready(db: DB, response: Response):
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable", "database": "down"}
    return {"status": "ok", "database": "up"}


@router.get("/metrics", include_in_schema=False)
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
