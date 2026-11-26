from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import cache, schemas
from app.api.deps import DB, CurrentUser, Paging, client_ip, get_or_404, require_editor
from app.config import get_settings
from app.models import User
from app.observability import RATE_LIMITED
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(tags=["auth & users"])
Editor = Annotated[User, Depends(require_editor)]


@router.post("/auth/login", response_model=schemas.TokenOut)
def login(request: Request, form: Annotated[OAuth2PasswordRequestForm, Depends()], db: DB):
    """OAuth2 password flow: send `username` (the email) and `password` as form fields."""
    email = form.username.strip().lower()
    # Tighter limit per IP + account, to slow down password guessing.
    retry_after = cache.hit_rate_limit(
        f"login:{client_ip(request)}:{email}", get_settings().login_rate_limit_per_minute
    )
    if retry_after is not None:
        RATE_LIMITED.labels("login").inc()
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many login attempts",
            headers={"Retry-After": str(retry_after)},
        )

    user = db.scalars(select(User).where(User.email == email)).first()
    # Same error for "no such user" and "wrong password": don't reveal which emails exist.
    if not verify_password(form.password, user.hashed_password if user else None):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User is deactivated")
    token, expires_in = create_access_token(user.id, user.role)
    return {"access_token": token, "token_type": "bearer", "expires_in": expires_in}


@router.post("/auth/register", response_model=schemas.UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: schemas.UserCreate, db: DB):
    """Self-service signup. Always creates a viewer; promotion to editor requires an
    existing editor via PATCH /users/{id}."""
    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        role="viewer",
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered") from None
    db.refresh(user)
    return user


@router.get("/auth/me", response_model=schemas.UserOut)
def me(user: CurrentUser):
    return user


@router.post("/users", response_model=schemas.UserOut, status_code=status.HTTP_201_CREATED)
def create_user(payload: schemas.UserCreate, db: DB, _: Editor):
    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered") from None
    db.refresh(user)
    return user


@router.get("/users", response_model=schemas.Page[schemas.UserOut])
def list_users(db: DB, page: Paging, _: Editor):
    total = db.scalar(select(func.count()).select_from(User))
    items = db.scalars(select(User).order_by(User.id).limit(page.limit).offset(page.offset)).all()
    return {"items": items, "total": total, "limit": page.limit, "offset": page.offset}


@router.patch("/users/{user_id}", response_model=schemas.UserOut)
def update_user(user_id: int, payload: schemas.UserUpdate, db: DB, editor: Editor):
    user = get_or_404(db, User, user_id)
    changes = payload.model_dump(exclude_unset=True)
    if user.id == editor.id and (
        changes.get("role") == "viewer" or changes.get("is_active") is False
    ):
        # Prevents the last editor from locking everyone out by accident.
        raise HTTPException(status.HTTP_409_CONFLICT, "You can't demote or deactivate yourself")
    if "password" in changes:
        user.hashed_password = hash_password(changes.pop("password"))
    for key, value in changes.items():
        setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return user
