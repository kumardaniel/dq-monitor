"""Logging and Prometheus metrics.

Logs are emitted as one JSON object per line, which log platforms (Datadog, Loki,
CloudWatch) can parse and filter without regexes. Every API request gets a request id,
returned in the X-Request-ID header and attached to its log lines.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextvars import ContextVar

from prometheus_client import Counter, Histogram
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# ---- Metrics ---------------------------------------------------------------
HTTP_REQUESTS = Counter("dq_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "dq_http_request_duration_seconds", "HTTP request latency", ["method", "route"]
)
CHECK_RESULTS = Counter(
    "dq_check_results_total", "Check results by outcome", ["rule_type", "status"]
)
CHECK_DURATION = Histogram(
    "dq_check_duration_seconds", "Time to execute a single check", ["rule_type"]
)
RUNS = Counter("dq_runs_total", "Check runs by final status and trigger", ["status", "trigger"])
ALERTS_OPENED = Counter("dq_alerts_opened_total", "Alerts opened", ["severity"])
NOTIFICATIONS = Counter("dq_notifications_total", "Alert notifications sent", ["outcome"])


# ---- Logging ---------------------------------------------------------------
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if rid := request_id_var.get():
            payload["request_id"] = rid
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())


def log_extra(**fields: object) -> dict:
    """Usage: logger.info("run finished", extra=log_extra(run_id=1, status="ok"))."""
    return {"extra_fields": fields}


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        token = request_id_var.set(rid)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            # Use the route template (/runs/{run_id}) not the raw path, so metric
            # cardinality stays bounded.
            route = getattr(request.scope.get("route"), "path", "unmatched")
            elapsed = time.perf_counter() - start
            HTTP_REQUESTS.labels(request.method, route, str(status)).inc()
            HTTP_LATENCY.labels(request.method, route).observe(elapsed)
            request_id_var.reset(token)
