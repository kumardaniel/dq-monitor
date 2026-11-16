"""Shared FastAPI dependencies."""

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Query, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(key: Annotated[str | None, Security(api_key_header)]) -> None:
    # compare_digest takes the same time whether the key is right or wrong,
    # so an attacker can't guess it byte by byte from response timings.
    if not key or not secrets.compare_digest(key, get_settings().api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )


DB = Annotated[Session, Depends(get_db)]


class Pagination:
    def __init__(
        self,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        self.limit = limit
        self.offset = offset


Paging = Annotated[Pagination, Depends()]


def get_or_404(db: Session, model, obj_id: int):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{model.__name__} {obj_id} not found")
    return obj
