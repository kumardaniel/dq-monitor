"""End-to-end API tests: HTTP request -> FastAPI -> Celery (eager) -> check engine -> DB."""

from unittest.mock import patch

from app.config import get_settings


def test_health_and_ready_need_no_auth(client):
    assert client.get("/health", headers={"X-API-Key": ""}).json() == {"status": "ok"}
    assert client.get("/ready").json()["database"] == "up"


def test_metrics_endpoint_exposes_prometheus_format(client):
    client.get("/health")
    body = client.get("/metrics").text
    assert "dq_http_requests_total" in body


def test_api_requires_key(client):
    assert client.get("/api/v1/data-sources", headers={"X-API-Key": "wrong"}).status_code == 401


def test_request_id_header_is_returned(client):
    resp = client.get("/health", headers={"X-Request-ID": "abc123"})
    assert resp.headers["X-Request-ID"] == "abc123"


def test_create_source_masks_password(client):
    resp = client.post(
        "/api/v1/data-sources",
        json={
            "name": "pg",
            "source_type": "postgresql",
            "connection_config": {"url": "postgresql://user:supersecret@db:5432/x"},
        },
    )
    assert resp.status_code == 201
    assert "supersecret" not in resp.text
    assert resp.json()["connection_config"]["url"] == "postgresql://user:***@db:5432/x"


def test_source_validation(client):
    bad_type = {"name": "a", "source_type": "oracle", "connection_config": {"url": "x"}}
    assert client.post("/api/v1/data-sources", json=bad_type).status_code == 422
    mismatch = {"name": "a", "source_type": "postgresql", "connection_config": {"url": "sqlite://"}}
    assert client.post("/api/v1/data-sources", json=mismatch).status_code == 422
    bad_cron = {
        "name": "a",
        "source_type": "sqlite",
        "connection_config": {"url": "sqlite://"},
        "schedule_cron": "every tuesday",
    }
    assert client.post("/api/v1/data-sources", json=bad_cron).status_code == 422


def test_duplicate_source_name_conflicts(client, source, warehouse_url):
    body = {
        "name": "warehouse",
        "source_type": "sqlite",
        "connection_config": {"url": warehouse_url},
    }
    assert client.post("/api/v1/data-sources", json=body).status_code == 409


def test_list_and_paginate_sources(client, warehouse_url):
    for i in range(3):
        client.post(
            "/api/v1/data-sources",
            json={
                "name": f"s{i}",
                "source_type": "sqlite",
                "connection_config": {"url": warehouse_url},
            },
        )
    page = client.get("/api/v1/data-sources?limit=2&offset=1").json()
    assert page["total"] == 3
    assert [s["name"] for s in page["items"]] == ["s1", "s2"]


def test_test_connection(client, source):
    assert client.post(f"/api/v1/data-sources/{source['id']}/test-connection").json() == {
        "ok": True
    }


def test_rule_crud_and_validation(client, source, make_rule):
    rule = make_rule(rule_type="not_null", column_name="customer_id", severity="critical")
    assert rule["severity"] == "critical"

    bad = {
        "data_source_id": source["id"],
        "name": "r",
        "rule_type": "range",
        "table_name": "orders",
        "column_name": "amount",
        "rule_config": {},
    }
    resp = client.post("/api/v1/rules", json=bad)
    assert resp.status_code == 422
    assert "at least one" in resp.text

    resp = client.patch(f"/api/v1/rules/{rule['id']}", json={"threshold": 0.5})
    assert resp.json()["threshold"] == 0.5
    assert (
        client.patch(f"/api/v1/rules/{rule['id']}", json={"column_name": "bad col"}).status_code
        == 422
    )

    listed = client.get(f"/api/v1/rules?data_source_id={source['id']}").json()
    assert listed["total"] == 1
    assert client.delete(f"/api/v1/rules/{rule['id']}").status_code == 204
    assert client.get(f"/api/v1/rules/{rule['id']}").status_code == 404


def test_rule_for_missing_source_404(client):
    body = {
        "data_source_id": 999,
        "name": "r",
        "rule_type": "row_count",
        "table_name": "orders",
        "rule_config": {"min": 1},
    }
    assert client.post("/api/v1/rules", json=body).status_code == 404


def test_rule_types_listed(client):
    types = {t["rule_type"] for t in client.get("/api/v1/rules/types").json()}
    assert {"not_null", "unique", "range", "freshness", "custom_sql"} <= types


def test_full_run_records_results_and_opens_alerts(client, source, make_rule):
    make_rule(name="ids present", rule_type="not_null", column_name="order_id")
    make_rule(
        name="customer set", rule_type="not_null", column_name="customer_id", severity="critical"
    )
    make_rule(name="ids unique", rule_type="unique", column_name="order_id")
    make_rule(name="broken", rule_type="not_null", column_name="no_such_column")

    resp = client.post(f"/api/v1/data-sources/{source['id']}/runs")
    assert resp.status_code == 202
    run = client.get(f"/api/v1/runs/{resp.json()['id']}").json()

    assert run["status"] == "completed"
    assert (run["total_rules"], run["passed_rules"], run["failed_rules"], run["error_rules"]) == (
        4,
        1,
        2,
        1,
    )
    by_status = {r["status"] for r in run["results"]}
    assert by_status == {"passed", "failed", "error"}

    alerts = client.get("/api/v1/alerts?status=open").json()
    assert alerts["total"] == 3  # 2 failed + 1 errored rule
    critical = client.get("/api/v1/alerts?severity=critical").json()["items"]
    assert len(critical) == 1


def test_alerts_deduplicate_and_auto_resolve(client, source, make_rule):
    rule = make_rule(rule_type="not_null", column_name="customer_id")

    for _ in range(3):
        client.post(f"/api/v1/data-sources/{source['id']}/runs")
    alerts = client.get("/api/v1/alerts").json()["items"]
    assert len(alerts) == 1, "repeat failures must not open new alerts"
    assert alerts[0]["occurrences"] == 3

    # Loosen the rule so it passes: the alert should auto-resolve.
    client.patch(f"/api/v1/rules/{rule['id']}", json={"threshold": 0.5})
    client.post(f"/api/v1/data-sources/{source['id']}/runs")
    alert = client.get("/api/v1/alerts").json()["items"][0]
    assert alert["status"] == "resolved"
    assert alert["resolved_at"] is not None

    history = client.get(f"/api/v1/rules/{rule['id']}/results").json()
    assert history["total"] == 4
    assert history["items"][0]["status"] == "passed"  # newest first


def test_alert_acknowledge_and_resolve_transitions(client, source, make_rule):
    make_rule(rule_type="not_null", column_name="customer_id")
    client.post(f"/api/v1/data-sources/{source['id']}/runs")
    alert_id = client.get("/api/v1/alerts").json()["items"][0]["id"]

    assert client.post(f"/api/v1/alerts/{alert_id}/acknowledge").json()["status"] == "acknowledged"
    assert client.post(f"/api/v1/alerts/{alert_id}/acknowledge").status_code == 409
    assert client.post(f"/api/v1/alerts/{alert_id}/resolve").json()["status"] == "resolved"


def test_webhook_notification_sent_once_per_incident(client, source, make_rule, monkeypatch):
    monkeypatch.setattr(get_settings(), "alert_webhook_url", "https://hooks.example.test/x")
    make_rule(name="customer set", rule_type="not_null", column_name="customer_id")

    with patch("app.services.alerting.httpx.post") as post:
        post.return_value.raise_for_status.return_value = None
        client.post(f"/api/v1/data-sources/{source['id']}/runs")
        client.post(f"/api/v1/data-sources/{source['id']}/runs")

    assert post.call_count == 1
    payload = post.call_args.kwargs["json"]
    assert payload["event"] == "opened"
    assert "customer set" in payload["text"]


def test_unreachable_source_marks_results_as_error(client, make_rule, tmp_path):
    resp = client.post(
        "/api/v1/data-sources",
        json={
            "name": "gone",
            "source_type": "sqlite",
            "connection_config": {"url": f"sqlite:///{tmp_path}/missing/dir/x.db"},
        },
    )
    source_id = resp.json()["id"]
    client.post(
        "/api/v1/rules",
        json={
            "data_source_id": source_id,
            "name": "rc",
            "rule_type": "row_count",
            "table_name": "orders",
            "rule_config": {"min": 1},
        },
    )
    run = client.post(f"/api/v1/data-sources/{source_id}/runs").json()
    detail = client.get(f"/api/v1/runs/{run['id']}").json()
    assert detail["status"] == "completed"
    assert detail["results"][0]["status"] == "error"


def test_dashboard_summary(client, source, make_rule):
    make_rule(rule_type="not_null", column_name="order_id")
    make_rule(rule_type="not_null", column_name="customer_id", severity="critical")
    client.post(f"/api/v1/data-sources/{source['id']}/runs")

    summary = client.get("/api/v1/dashboard/summary").json()
    assert summary["active_rules"] == 2
    assert summary["open_alerts"] == 1
    assert summary["critical_open_alerts"] == 1
    assert summary["pass_rate_last_24h"] == 0.5
    assert summary["sources"][0]["pass_rate"] == 0.5
    assert summary["sources"][0]["open_alerts"] == 1


def test_deleting_source_cascades(client, source, make_rule):
    make_rule(rule_type="not_null", column_name="order_id")
    client.post(f"/api/v1/data-sources/{source['id']}/runs")
    assert client.delete(f"/api/v1/data-sources/{source['id']}").status_code == 204
    assert client.get("/api/v1/rules").json()["total"] == 0
    assert client.get("/api/v1/runs").json()["total"] == 0
