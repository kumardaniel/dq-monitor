"""Test fixtures.

Two SQLite databases are used:
- the *metadata* DB (DQ Monitor's own tables), recreated for every test;
- a *warehouse* DB playing the monitored data source, seeded with known-bad data.

Celery runs in eager mode, so triggering a run executes it synchronously and the test
can assert on the results straight away.
"""

import os
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="dq-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'meta.db'}"
os.environ["ENVIRONMENT"] = "test"
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"
os.environ["BCRYPT_ROUNDS"] = "4"  # the minimum: keeps the suite fast
os.environ["RATE_LIMIT_PER_MINUTE"] = "10000"
os.environ["LOGIN_RATE_LIMIT_PER_MINUTE"] = "10000"
for var in ("ALERT_WEBHOOK_URL", "REDIS_URL", "BOOTSTRAP_EDITOR_EMAIL"):
    os.environ.pop(var, None)

import fakeredis  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import cache  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402
from app.security import create_access_token, hash_password  # noqa: E402

PASSWORD = "correct-horse-battery"

WAREHOUSE = _tmp / "warehouse.db"


def build_warehouse(path: Path = WAREHOUSE) -> str:
    """orders: 10 rows with exactly-known defects so every check has a precise answer.

    - customer_id NULL in 2 rows
    - order_id 7 duplicated (rows 7 and 8)          -> 1 duplicate row
    - amount out of [0, 1000] in 1 row (-5) and 1 row (2500)
    - status 'lost' is not an accepted value        -> 1 row
    - email malformed in 1 row
    - newest updated_at is 10 minutes old
    """
    path.unlink(missing_ok=True)
    now = datetime.now(UTC)
    rows = [
        (1, 101, 20.0, "paid", "a@x.com"),
        (2, 102, 35.5, "paid", "b@x.com"),
        (3, None, 12.0, "shipped", "c@x.com"),
        (4, 104, -5.0, "paid", "d@x.com"),
        (5, 105, 2500.0, "refunded", "e@x.com"),
        (6, None, 80.0, "paid", "not-an-email"),
        (7, 107, 15.0, "lost", "g@x.com"),
        (7, 108, 15.0, "paid", "h@x.com"),
        (9, 109, 99.0, "shipped", "i@x.com"),
        (10, 110, 1.0, "paid", "j@x.com"),
    ]
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE orders (order_id INTEGER, customer_id INTEGER, amount REAL, "
        "status TEXT, email TEXT, updated_at TIMESTAMP)"
    )
    for i, r in enumerate(rows):
        ts = (now - timedelta(minutes=10 + i)).isoformat()
        con.execute("INSERT INTO orders VALUES (?,?,?,?,?,?)", (*r, ts))
    con.execute("CREATE TABLE empty_table (id INTEGER)")
    con.commit()
    con.close()
    return f"sqlite:///{path}"


@pytest.fixture(autouse=True)
def _fresh_metadata_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def warehouse_url() -> str:
    return build_warehouse()


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def redis_client():
    """An in-memory fake Redis, fresh for every test, so the cache path is exercised."""
    fake = fakeredis.FakeRedis(decode_responses=True)
    cache.set_client(fake)
    yield fake
    cache.set_client(None)


def _make_user(email: str, role: str) -> User:
    with SessionLocal() as s:
        user = User(email=email, hashed_password=hash_password(PASSWORD), role=role)
        s.add(user)
        s.commit()
        s.refresh(user)
        return user


def _auth(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)[0]}"}


@pytest.fixture
def editor() -> User:
    return _make_user("editor@example.com", "editor")


@pytest.fixture
def viewer() -> User:
    return _make_user("viewer@example.com", "viewer")


@pytest.fixture
def anon_client() -> TestClient:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client(editor) -> TestClient:
    """Authenticated as an editor (can read and write)."""
    with TestClient(app, headers=_auth(editor)) as c:
        yield c


@pytest.fixture
def viewer_client(viewer) -> TestClient:
    with TestClient(app, headers=_auth(viewer)) as c:
        yield c


@pytest.fixture
def source(client, warehouse_url) -> dict:
    resp = client.post(
        "/api/v1/data-sources",
        json={
            "name": "warehouse",
            "source_type": "sqlite",
            "connection_config": {"url": warehouse_url},
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture
def make_rule(client, source):
    def _make(**overrides) -> dict:
        body = {"data_source_id": source["id"], "name": "rule", "table_name": "orders", **overrides}
        resp = client.post("/api/v1/rules", json=body)
        assert resp.status_code == 201, resp.text
        return resp.json()

    return _make
