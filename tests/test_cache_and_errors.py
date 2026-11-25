"""Redis cache (hits, misses, invalidation, fail-open), rate limiting, and the error format."""

import redis
from fastapi.testclient import TestClient

from app import cache
from app.config import get_settings


def test_status_cache_miss_then_hit(client, source, redis_client):
    url = f"/api/v1/data-sources/{source['id']}/status"
    first = client.get(url)
    assert first.headers["X-Cache"] == "MISS"
    assert first.json()["health"] == "unknown"
    assert redis_client.ttl(cache.source_status_key(source["id"])) > 0  # stored with a TTL

    second = client.get(url)
    assert second.headers["X-Cache"] == "HIT"
    assert second.json() == first.json()


def test_new_run_invalidates_status(client, source, make_rule):
    make_rule(rule_type="not_null", column_name="customer_id", severity="critical")
    url = f"/api/v1/data-sources/{source['id']}/status"
    client.get(url)
    assert client.get(url).headers["X-Cache"] == "HIT"

    client.post(f"/api/v1/data-sources/{source['id']}/runs")

    after = client.get(url)
    assert after.headers["X-Cache"] == "MISS"  # the run invalidated the cached entry
    body = after.json()
    assert body["health"] == "failing"
    assert body["last_run"]["status"] == "completed"
    assert body["open_alerts"] == 1 and body["critical_open_alerts"] == 1


def test_alert_resolution_invalidates_status_and_summary(client, source, make_rule):
    make_rule(rule_type="not_null", column_name="customer_id")
    client.post(f"/api/v1/data-sources/{source['id']}/runs")
    status_url = f"/api/v1/data-sources/{source['id']}/status"
    for url in (status_url, "/api/v1/dashboard/summary"):
        client.get(url)
        assert client.get(url).headers["X-Cache"] == "HIT"

    alert_id = client.get("/api/v1/alerts").json()["items"][0]["id"]
    client.post(f"/api/v1/alerts/{alert_id}/resolve")

    status = client.get(status_url)
    summary = client.get("/api/v1/dashboard/summary")
    assert status.headers["X-Cache"] == summary.headers["X-Cache"] == "MISS"
    assert status.json()["open_alerts"] == 0
    assert summary.json()["open_alerts"] == 0


def test_rule_change_invalidates_summary(client, source, make_rule):
    client.get("/api/v1/dashboard/summary")
    make_rule(rule_type="not_null", column_name="order_id")
    resp = client.get("/api/v1/dashboard/summary")
    assert resp.headers["X-Cache"] == "MISS"
    assert resp.json()["active_rules"] == 1


def test_healthy_source(client, source, make_rule):
    make_rule(rule_type="not_null", column_name="order_id")
    client.post(f"/api/v1/data-sources/{source['id']}/runs")
    assert client.get(f"/api/v1/data-sources/{source['id']}/status").json()["health"] == "healthy"


class BrokenRedis:
    """Every command raises, like a Redis server that is down."""

    def __getattr__(self, _name):
        def fail(*_a, **_k):
            raise redis.ConnectionError("redis down")

        return fail


def test_redis_outage_fails_open(client, source):
    cache.set_client(BrokenRedis())
    resp = client.get(f"/api/v1/data-sources/{source['id']}/status")
    assert resp.status_code == 200  # served from the DB, not an error
    assert resp.headers["X-Cache"] == "MISS"
    assert client.post(f"/api/v1/data-sources/{source['id']}/runs").status_code == 202


def test_cache_disabled_without_redis(client, source):
    cache.set_client(None)
    url = f"/api/v1/data-sources/{source['id']}/status"
    assert client.get(url).headers["X-Cache"] == "MISS"
    assert client.get(url).headers["X-Cache"] == "MISS"


def test_api_rate_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_per_minute", 5)
    codes = [client.get("/api/v1/data-sources").status_code for _ in range(6)]
    assert codes == [200] * 5 + [429]
    resp = client.get("/api/v1/data-sources")
    assert resp.json()["error"]["code"] == "rate_limited"
    assert "Retry-After" in resp.headers


# ---- Error format -----------------------------------------------------------
def test_not_found_error_shape(client):
    resp = client.get("/api/v1/rules/999", headers={"X-Request-ID": "req-42"})
    assert resp.status_code == 404
    assert resp.json() == {
        "error": {
            "code": "not_found",
            "message": "ValidationRule 999 not found",
            "request_id": "req-42",
        }
    }


def test_validation_error_shape(client, source):
    resp = client.post(
        "/api/v1/rules",
        json={"data_source_id": source["id"], "name": "r", "rule_type": "range",
              "table_name": "orders", "column_name": "amount", "threshold": 2},
    )  # fmt: skip
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "validation_error"
    assert "body.threshold" in {d["field"] for d in error["details"]}


def test_unknown_route_uses_same_shape(client):
    resp = client.get("/api/v1/nope")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_unhandled_exception_hides_internals(client, monkeypatch):
    from app.api.routes import runs
    from app.main import app

    def boom(*_a, **_k):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(runs, "select", boom)
    with TestClient(app, headers=dict(client.headers), raise_server_exceptions=False) as c:
        resp = c.get("/api/v1/runs")
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "internal_error"
    assert "secret internal detail" not in resp.text
