"""Shared FastAPI dependencies: database session, auth, roles, rate limiting, paging."""

from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app import cache
from app.config import get_settings
from app.database import get_db
from app.models import User
from app.observability import RATE_LIMITED
from app.security import TokenError, decode_access_token

DB = Annotated[Session, Depends(get_db)]

# tokenUrl makes Swagger's "Authorize" button show a username/password login form.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(token: Annotated[str | None, Depends(oauth2_scheme)], db: DB) -> User:
    if not token:
        raise _unauthorized("Not authenticated")
    try:
        user_id = decode_access_token(token)
    except TokenError as exc:
        raise _unauthorized(str(exc)) from None
    # Re-load the user on every request: deactivation and role changes take effect
    # immediately instead of when the (unrevocable) token expires.
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _unauthorized("User not found or inactive")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_editor(user: CurrentUser) -> User:
    if user.role != "editor":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Editor role required")
    return user


def enforce_role_by_method(request: Request, user: CurrentUser) -> User:
    """Router-wide rule: any authenticated user may read; only editors may write.

    Applied once per router instead of on every endpoint, so a new write endpoint
    can't accidentally ship without the editor check.
    """
    if request.method not in READ_METHODS:
        require_editor(user)
    return user


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def rate_limit(request: Request) -> None:
    retry_after = cache.hit_rate_limit(
        f"ip:{client_ip(request)}", get_settings().rate_limit_per_minute
    )
    if retry_after is not None:
        RATE_LIMITED.labels("api").inc()
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )


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
