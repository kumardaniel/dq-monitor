"""Seed a demo data source + rules through the public API, then trigger a run.

Local (SQLite, no Docker):
    python scripts/seed_demo.py
Docker stack (monitors the Postgres "warehouse" DB from deploy/postgres-init):
    python scripts/seed_demo.py \
        --source-type postgresql \
        --source-url postgresql://dq_reader:dq_reader@postgres:5432/warehouse
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

RULES = [
    {"name": "order_id is never null", "rule_type": "not_null", "column_name": "order_id",
     "severity": "critical"},
    {"name": "order_id is unique", "rule_type": "unique", "column_name": "order_id",
     "severity": "critical"},
    {"name": "customer_id mostly present", "rule_type": "not_null",
     "column_name": "customer_id", "threshold": 0.01},
    {"name": "amount is non-negative", "rule_type": "range", "column_name": "amount",
     "rule_config": {"min": 0, "max": 10000}},
    {"name": "status is a known value", "rule_type": "accepted_values", "column_name": "status",
     "rule_config": {"values": ["paid", "shipped", "refunded"]}},
    {"name": "email looks valid", "rule_type": "regex", "column_name": "email",
     "rule_config": {"pattern": "^[^@ ]+@[^@ ]+\\.[a-z]+$"}},
    {"name": "orders table is not empty", "rule_type": "row_count",
     "rule_config": {"min": 100}},
    {"name": "orders updated in the last hour", "rule_type": "freshness",
     "column_name": "updated_at", "rule_config": {"max_age_minutes": 60}},
    {"name": "no refunds above order value", "rule_type": "custom_sql",
     "rule_config": {"sql": "SELECT * FROM orders WHERE status = 'refunded' AND amount > 450"}},
]  # fmt: skip


def build_sqlite_warehouse(path: Path) -> str:
    path.unlink(missing_ok=True)
    random.seed(7)
    now = datetime.now(UTC)
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE orders (order_id INTEGER, customer_id INTEGER, amount REAL, "
        "status TEXT, email TEXT, updated_at TIMESTAMP)"
    )
    statuses = ["paid", "shipped", "refunded", "paid", "lost"]
    for g in range(1, 2001):
        con.execute(
            "INSERT INTO orders VALUES (?,?,?,?,?,?)",
            (
                g if g != 2000 else 1999,  # one duplicate id
                None if g % 50 == 0 else 1000 + g,
                -10.0 if g % 97 == 0 else round(random.uniform(1, 500), 2),
                statuses[g % 5],
                "broken-email" if g % 120 == 0 else f"user{g}@example.com",
                (now - timedelta(seconds=g)).isoformat(),
            ),
        )
    con.commit()
    con.close()
    return f"sqlite:///{path.resolve()}"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://localhost:8000")
    p.add_argument("--key", default="dev-api-key")
    p.add_argument("--source-type", default="sqlite")
    p.add_argument("--source-url", help="defaults to a freshly built ./demo_warehouse.db")
    p.add_argument("--name", default="demo-warehouse")
    args = p.parse_args()

    url = args.source_url or build_sqlite_warehouse(Path("demo_warehouse.db"))
    api = httpx.Client(base_url=args.api, headers={"X-API-Key": args.key}, timeout=30)

    resp = api.post(
        "/api/v1/data-sources",
        json={
            "name": args.name,
            "source_type": args.source_type,
            "connection_config": {"url": url},
            "schedule_cron": "*/5 * * * *",
        },
    )
    if resp.status_code == 409:
        source = next(s for s in api.get("/api/v1/data-sources").json()["items"]
                      if s["name"] == args.name)  # fmt: skip
        print(f"Using existing data source {source['id']}")
    else:
        resp.raise_for_status()
        source = resp.json()
        print(f"Created data source {source['id']} ({source['connection_config']['url']})")
        for rule in RULES:
            api.post(
                "/api/v1/rules",
                json={"data_source_id": source["id"], "table_name": "orders", **rule},
            ).raise_for_status()
        print(f"Created {len(RULES)} rules")

    run = api.post(f"/api/v1/data-sources/{source['id']}/runs")
    if run.status_code == 409:
        print("A run is already in progress; try again shortly.")
        return
    run.raise_for_status()
    run_id = run.json()["id"]
    for _ in range(60):  # poll until a background worker finishes it
        detail = api.get(f"/api/v1/runs/{run_id}").json()
        if detail["status"] in ("completed", "failed"):
            break
        time.sleep(1)

    print(f"\nRun {run_id}: {detail['status']}  passed={detail['passed_rules']} "
          f"failed={detail['failed_rules']} errors={detail['error_rules']}")  # fmt: skip
    for r in detail.get("results", []):
        print(f"  [{r['status']:>6}] {r['message']}")
    print("\nDashboard summary:")
    print(json.dumps(api.get("/api/v1/dashboard/summary").json(), indent=2))


if __name__ == "__main__":
    main()
