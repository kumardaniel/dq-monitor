"""FastAPI application entrypoint.

Run locally:  uvicorn app.main:app --reload
Docs:         http://localhost:8000/docs  (Swagger UI, generated from the code)
"""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from app.api.deps import require_api_key
from app.api.routes import alerts, data_sources, health, rules, runs
from app.config import get_settings
from app.engine.connectors import dispose_all
from app.observability import RequestContextMiddleware, configure_logging

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging(settings.log_level)
    yield
    dispose_all()  # close pooled connections to monitored sources on shutdown


def create_app() -> FastAPI:
    app = FastAPI(
        title="DQ Monitor",
        description=(
            "Data quality monitoring service: register data sources, define validation "
            "rules, run them on a schedule, and get alerted when data breaks."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(RequestContextMiddleware)

    app.include_router(health.router)  # unauthenticated: probes and Prometheus
    protected = [Depends(require_api_key)]
    for module in (data_sources, rules, runs, alerts):
        app.include_router(module.router, prefix="/api/v1", dependencies=protected)
    return app


app = create_app()
