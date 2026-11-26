"""JWT login, token validation, roles and user management."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.config import get_settings
from app.database import SessionLocal
from app.main import check_production_config
from app.models import User
from app.security import create_access_token, hash_password, verify_password
from tests.conftest import PASSWORD


def login(client, email, password=PASSWORD):
    return client.post("/api/v1/auth/login", data={"username": email, "password": password})


def test_password_is_hashed_not_stored(editor):
    assert editor.hashed_password != PASSWORD
    assert editor.hashed_password.startswith("$2b$")  # bcrypt
    assert verify_password(PASSWORD, editor.hashed_password)
    assert not verify_password("wrong-password", editor.hashed_password)
    assert not verify_password(PASSWORD, None)  # unknown user path


def test_login_returns_working_token(anon_client, editor):
    resp = login(anon_client, "EDITOR@example.com ")  # email is case/space-insensitive
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == 3600

    me = anon_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.json()["email"] == "editor@example.com"
    assert "hashed_password" not in me.text


def test_wrong_password_and_unknown_user_look_identical(anon_client, editor):
    wrong = login(anon_client, "editor@example.com", "nope-nope-nope")
    unknown = login(anon_client, "ghost@example.com")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]


def test_protected_routes_reject_missing_or_bad_tokens(anon_client, editor):
    assert anon_client.get("/api/v1/data-sources").status_code == 401
    bad = {"Authorization": "Bearer not.a.jwt"}
    assert anon_client.get("/api/v1/data-sources", headers=bad).status_code == 401
    resp = anon_client.get("/api/v1/data-sources")
    assert resp.headers["WWW-Authenticate"] == "Bearer"


def test_expired_token_rejected(anon_client, editor):
    s = get_settings()
    token = jwt.encode(
        {"sub": str(editor.id), "type": "access", "exp": datetime.now(UTC) - timedelta(seconds=1)},
        s.jwt_secret,
        algorithm=s.jwt_algorithm,
    )
    resp = anon_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["error"]["message"] == "Token expired"


def test_token_signed_with_other_key_rejected(anon_client, editor):
    forged = jwt.encode(
        {"sub": str(editor.id), "type": "access", "exp": datetime.now(UTC) + timedelta(hours=1)},
        "attacker-key-attacker-key-attacker-key",
        algorithm="HS256",
    )
    resp = anon_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 401


def test_unsigned_none_algorithm_token_rejected(anon_client, editor):
    unsigned = jwt.encode(
        {"sub": str(editor.id), "type": "access", "exp": datetime.now(UTC) + timedelta(hours=1)},
        None,
        algorithm="none",
    )
    resp = anon_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {unsigned}"})
    assert resp.status_code == 401


def test_deactivated_user_locked_out_immediately(anon_client, editor):
    headers = {"Authorization": f"Bearer {create_access_token(editor.id, 'editor')[0]}"}
    assert anon_client.get("/api/v1/auth/me", headers=headers).status_code == 200
    with SessionLocal() as s:
        s.get(User, editor.id).is_active = False
        s.commit()
    # Same, still-unexpired token is now refused because the user is re-checked per request.
    assert anon_client.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert login(anon_client, "editor@example.com").status_code == 403


def test_viewer_can_read_but_not_write(viewer_client, client, source, make_rule):
    rule = make_rule(rule_type="not_null", column_name="order_id")
    assert viewer_client.get("/api/v1/data-sources").status_code == 200
    assert viewer_client.get(f"/api/v1/rules/{rule['id']}").status_code == 200
    assert viewer_client.get("/api/v1/dashboard/summary").status_code == 200

    writes = [
        ("post", "/api/v1/rules", {"data_source_id": source["id"], "name": "x",
                                   "rule_type": "row_count", "table_name": "orders",
                                   "rule_config": {"min": 1}}),
        ("patch", f"/api/v1/rules/{rule['id']}", {"threshold": 0.5}),
        ("delete", f"/api/v1/rules/{rule['id']}", None),
        ("post", f"/api/v1/data-sources/{source['id']}/runs", None),
        ("delete", f"/api/v1/data-sources/{source['id']}", None),
    ]  # fmt: skip
    for method, url, body in writes:
        kwargs = {"json": body} if body is not None else {}
        resp = getattr(viewer_client, method)(url, **kwargs)
        assert resp.status_code == 403, (method, url)
        assert resp.json()["error"]["code"] == "forbidden"


def test_editor_manages_users(client, anon_client):
    resp = client.post(
        "/api/v1/users", json={"email": "New@Example.com", "password": "longenough1"}
    )
    assert resp.status_code == 201
    assert resp.json()["email"] == "new@example.com"
    assert resp.json()["role"] == "viewer"
    assert login(anon_client, "new@example.com", "longenough1").status_code == 200

    dup = client.post("/api/v1/users", json={"email": "new@example.com", "password": "x" * 10})
    assert dup.status_code == 409

    weak = client.post("/api/v1/users", json={"email": "a@b.co", "password": "short"})
    assert weak.status_code == 422
    assert weak.json()["error"]["details"][0]["field"] == "body.password"

    user_id = resp.json()["id"]
    promoted = client.patch(f"/api/v1/users/{user_id}", json={"role": "editor"})
    assert promoted.json()["role"] == "editor"
    assert client.get("/api/v1/users").json()["total"] == 2


def test_register_creates_viewer_user(anon_client):
    resp = anon_client.post(
        "/api/v1/auth/register", json={"email": "self@example.com", "password": "longenough1"}
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "viewer"
    assert login(anon_client, "self@example.com", "longenough1").status_code == 200


def test_register_duplicate_email_conflict(anon_client):
    body = {"email": "dup@example.com", "password": "longenough1"}
    assert anon_client.post("/api/v1/auth/register", json=body).status_code == 201
    dup = anon_client.post("/api/v1/auth/register", json=body)
    assert dup.status_code == 409


def test_register_ignores_role_override(anon_client):
    body = {"email": "sneaky@example.com", "password": "longenough1", "role": "editor"}
    resp = anon_client.post("/api/v1/auth/register", json=body)
    assert resp.status_code == 201
    assert resp.json()["role"] == "viewer"


def test_viewer_cannot_manage_users(viewer_client):
    assert viewer_client.get("/api/v1/users").status_code == 403
    body = {"email": "x@example.com", "password": "longenough1", "role": "editor"}
    assert viewer_client.post("/api/v1/users", json=body).status_code == 403


def test_editor_cannot_lock_themselves_out(client, editor):
    assert client.patch(f"/api/v1/users/{editor.id}", json={"role": "viewer"}).status_code == 409
    assert client.patch(f"/api/v1/users/{editor.id}", json={"is_active": False}).status_code == 409


def test_login_rate_limited(anon_client, editor, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_rate_limit_per_minute", 3)
    codes = [
        login(anon_client, "editor@example.com", "bad-guess-123").status_code for _ in range(4)
    ]
    assert codes == [401, 401, 401, 429]
    resp = login(anon_client, "editor@example.com")  # even the right password is blocked now
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) > 0


def test_bootstrap_creates_first_editor_only_when_empty(monkeypatch):
    from app import main

    monkeypatch.setattr(main.settings, "bootstrap_editor_email", "Boss@Example.com")
    monkeypatch.setattr(main.settings, "bootstrap_editor_password", "bootstrap-pass")
    main.bootstrap_first_user()
    main.bootstrap_first_user()  # second call is a no-op
    with SessionLocal() as s:
        users = s.query(User).all()
    assert [(u.email, u.role) for u in users] == [("boss@example.com", "editor")]


def test_production_refuses_default_secret(monkeypatch):
    from app import main

    monkeypatch.setattr(main.settings, "environment", "production")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        check_production_config()
    monkeypatch.setattr(main.settings, "jwt_secret", "x" * 40)
    check_production_config()


def test_cli_create_user():
    from app.cli import create_user

    assert create_user("cli@example.com", "cli-password", "editor", False) == 0
    assert create_user("cli@example.com", "cli-password", "editor", True) == 0
    assert create_user("cli@example.com", "cli-password", "editor", False) == 1
    with SessionLocal() as s:
        user = s.query(User).filter_by(email="cli@example.com").one()
    assert user.role == "editor"
    assert verify_password("cli-password", user.hashed_password)
    assert user.hashed_password != hash_password("cli-password")  # salted: never identical
