"""FastAPI application entrypoint.

Run locally:  uvicorn app.main:app --reload
Docs:         http://localhost:8000/docs  (Swagger UI, generated from the code)
"""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.api import errors
from app.api.deps import enforce_role_by_method, rate_limit
from app.api.routes import alerts, auth, data_sources, health, rules, runs
from app.config import get_settings
from app.database import SessionLocal
from app.engine.connectors import dispose_all
from app.models import User
from app.observability import RequestContextMiddleware, configure_logging
from app.security import hash_password

logger = logging.getLogger(__name__)
settings = get_settings()
INSECURE_DEFAULT_SECRET = "dev-insecure-secret-change-me-before-deploying"


def check_production_config() -> None:
    """Refuse to start a non-local deployment with a guessable token-signing key."""
    if settings.environment in ("local", "test"):
        return
    if settings.jwt_secret == INSECURE_DEFAULT_SECRET or len(settings.jwt_secret) < 32:
        raise RuntimeError("Set JWT_SECRET to a random value of at least 32 characters")


def bootstrap_first_user() -> None:
    """Create the first editor from env vars, but only into an empty users table."""
    email, password = settings.bootstrap_editor_email, settings.bootstrap_editor_password
    if not (email and password):
        return
    try:
        with SessionLocal() as db:
            if db.scalar(select(func.count()).select_from(User)):
                return
            db.add(
                User(email=email.lower(), hashed_password=hash_password(password), role="editor")
            )
            db.commit()
            logger.info("bootstrapped first editor user")
    except SQLAlchemyError:
        logger.exception("could not bootstrap first user (are migrations applied?)")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging(settings.log_level)
    check_production_config()
    bootstrap_first_user()
    yield
    dispose_all()  # close pooled connections to monitored sources on shutdown


def create_app() -> FastAPI:
    app = FastAPI(
        title="DQ Monitor",
        description=(
            "Data quality monitoring service: register data sources, define validation "
            "rules, run them on a schedule, and get alerted when data breaks.\n\n"
            "**Auth:** click *Authorize*, log in with your email as the username. "
            "Viewers can read everything; only editors can create, change or trigger."
        ),
        version="2.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(RequestContextMiddleware)
    errors.install(app)

    app.include_router(health.router)  # unauthenticated: probes and Prometheus
    # Login + user management handle their own auth (login must work without a token).
    app.include_router(auth.router, prefix="/api/v1", dependencies=[Depends(rate_limit)])
    protected = [Depends(rate_limit), Depends(enforce_role_by_method)]
    for module in (data_sources, rules, runs, alerts):
        app.include_router(module.router, prefix="/api/v1", dependencies=protected)
    return app


app = create_app()
