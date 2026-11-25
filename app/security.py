"""Password hashing and JWT access tokens.

Passwords: bcrypt, a deliberately slow hash with a per-password salt, so a leaked
users table can't be reversed with precomputed tables and is expensive to brute-force.

Tokens: a signed JWT (HS256) carrying the user id. The server stores no session; any
API replica can verify a token with the shared secret. The trade-off: a token can't be
revoked before it expires, which is why tokens are short-lived and every request also
re-loads the user (a deactivated user is locked out immediately).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.config import get_settings

ROLES = ("viewer", "editor")
# Pre-computed hash used when the email doesn't exist, so a login for an unknown user
# takes as long as a wrong password and response times don't reveal which emails exist.
_DUMMY_HASH = bcrypt.hashpw(
    b"dummy-password", bcrypt.gensalt(get_settings().bcrypt_rounds)
).decode()


class TokenError(Exception):
    pass


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(get_settings().bcrypt_rounds)).decode()


def verify_password(password: str, hashed: str | None) -> bool:
    try:
        ok = bcrypt.checkpw(password.encode(), (hashed or _DUMMY_HASH).encode())
    except ValueError:  # bcrypt rejects passwords over 72 bytes
        return False
    return ok and hashed is not None


def create_access_token(user_id: int, role: str) -> tuple[str, int]:
    settings = get_settings()
    now = datetime.now(UTC)
    expires_in = settings.access_token_minutes * 60
    claims = {
        "sub": str(user_id),
        "role": role,  # informational for clients; the server re-reads the role from the DB
        "type": "access",
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    token = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_in


def decode_access_token(token: str) -> int:
    settings = get_settings()
    try:
        # algorithms= is a whitelist: never let the token header choose (e.g. "none").
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("Invalid token") from exc
    if claims.get("type") != "access":
        raise TokenError("Invalid token type")
    try:
        return int(claims["sub"])
    except (TypeError, ValueError) as exc:
        raise TokenError("Invalid token subject") from exc
