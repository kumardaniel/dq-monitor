"""Connections to the *monitored* databases (not DQ Monitor's own metadata DB).

Engines are cached per URL: creating an engine builds a connection pool, which is
expensive, so we build one per data source and reuse it across runs.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url

from app.config import get_settings

SUPPORTED_SOURCE_TYPES = {"postgresql": "postgresql", "sqlite": "sqlite"}

_engines: dict[str, Engine] = {}
_lock = threading.Lock()


class ConnectionConfigError(ValueError):
    pass


def validate_connection(source_type: str, connection_config: dict) -> str:
    if source_type not in SUPPORTED_SOURCE_TYPES:
        raise ConnectionConfigError(
            f"Unsupported source_type {source_type!r}. "
            f"Supported: {', '.join(SUPPORTED_SOURCE_TYPES)}."
        )
    url = connection_config.get("url")
    if not isinstance(url, str) or not url:
        raise ConnectionConfigError("connection_config must contain a 'url' string.")
    try:
        parsed = make_url(url)
    except Exception as exc:  # sqlalchemy raises ArgumentError with a vague message
        raise ConnectionConfigError(f"Invalid connection URL: {exc}") from exc
    if parsed.get_backend_name() != SUPPORTED_SOURCE_TYPES[source_type]:
        raise ConnectionConfigError(
            f"URL backend '{parsed.get_backend_name()}' does not match source_type '{source_type}'."
        )
    return url


def mask_url(url: str) -> str:
    """Hide the password so connection strings are safe to return from the API."""
    try:
        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        return "***"


def get_engine(url: str) -> Engine:
    with _lock:
        engine = _engines.get(url)
        if engine is None:
            connect_args = {}
            if make_url(url).get_backend_name() == "postgresql":
                # Server-side guard: Postgres cancels any check query running longer
                # than this, so one slow rule can't hang a worker forever.
                timeout_ms = int(get_settings().check_statement_timeout_ms)
                connect_args["options"] = f"-c statement_timeout={timeout_ms}"
            engine = create_engine(
                url, pool_pre_ping=True, pool_recycle=1800, connect_args=connect_args
            )
            _engines[url] = engine
        return engine


@contextmanager
def source_connection(url: str) -> Iterator[Connection]:
    """A connection whose transaction is always rolled back: checks never write."""
    engine = get_engine(url)
    with engine.connect() as conn:
        try:
            yield conn
        finally:
            conn.rollback()


def test_connection(url: str) -> None:
    with source_connection(url) as conn:
        conn.execute(text("SELECT 1"))


def dispose_all() -> None:
    with _lock:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()
