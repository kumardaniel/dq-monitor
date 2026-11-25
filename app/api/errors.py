"""One error format for every failure.

Every non-2xx response has this body, whether it came from an HTTPException in a
route, a request that failed Pydantic validation, or an unexpected crash:

    {
      "error": {
        "code": "validation_error",        # stable, machine-readable
        "message": "Request validation failed",
        "details": [{"field": "body.threshold", "message": "..."}],   # optional
        "request_id": "9f2c..."             # matches X-Request-ID and the logs
      }
    }

Clients can branch on `code` without parsing English messages.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.observability import request_id_var

logger = logging.getLogger(__name__)

CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    503: "service_unavailable",
}


def error_body(status: int, message: str, details: list | None = None) -> dict:
    error = {"code": CODES.get(status, f"http_{status}"), "message": message}
    if details:
        error["details"] = details
    if rid := request_id_var.get():
        error["request_id"] = rid
    return {"error": error}


async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(
        error_body(exc.status_code, message), status_code=exc.status_code, headers=exc.headers
    )


async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {
            "field": ".".join(str(p) for p in err["loc"]),
            # Pydantic prefixes custom errors with "Value error, "; drop it for readability.
            "message": err["msg"].removeprefix("Value error, "),
        }
        for err in exc.errors()
    ]
    return JSONResponse(error_body(422, "Request validation failed", details), status_code=422)


async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error")
    # Never leak internals (stack traces, SQL) to the client; the request_id links
    # the response to the full traceback in the logs.
    return JSONResponse(error_body(500, "Internal server error"), status_code=500)


def install(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unhandled)
