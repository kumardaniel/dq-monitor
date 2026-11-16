# DQ Monitor

[![CI](../../actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)

A backend service for **data quality monitoring**. You register databases as data sources,
define validation rules (not-null, uniqueness, ranges, freshness, custom SQL, and more),
and DQ Monitor runs them on a schedule with background workers. It records every result
and opens a deduplicated alert, with an optional Slack webhook, when data breaks. When the
data recovers, it resolves the alert on its own.

**Stack:** Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2.0 · PostgreSQL · Alembic ·
Celery · Redis · Prometheus · Docker Compose · pytest · Ruff · GitHub Actions

```
 client ──HTTP──▶ FastAPI ──enqueue──▶ Redis ──▶ Celery workers ──SQL──▶ monitored DBs
                     │                               │
                     └────────── PostgreSQL ◀────────┘   (sources, rules, runs, results, alerts)
                                      ▲
                     Celery beat ─────┘  (cron schedules)        Prometheus ◀── /metrics
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design, [docs/TOOLS.md](docs/TOOLS.md)
for a guide to every tool and where it is used, and
[docs/DQ_Monitor_Requirements.pdf](docs/DQ_Monitor_Requirements.pdf) for the requirements spec.

## Features

- **8 check types**, all pushed down to the database as SQL: `not_null`, `unique`, `range`,
  `accepted_values`, `regex`, `row_count`, `freshness`, `custom_sql`
- **Failure thresholds**: a rule can tolerate a set fraction of bad rows (for example 1%)
- **Sample bad values** saved with each failing result, so you can debug without re-querying
- **Async execution**: runs are queued to Celery workers, and the API returns `202 Accepted` right away
- **Cron scheduling** per data source through Celery beat, with no pile-up of overlapping runs
- **Alert lifecycle**: open → acknowledged → resolved, with deduplication and auto-resolve
- **Webhook notifications** (Slack-compatible), retried with exponential backoff
- **Security**: API-key auth, passwords masked in responses, SQL identifiers validated,
  values sent as bound parameters, read-only transactions for custom SQL
- **Observability**: JSON logs with request IDs, Prometheus metrics, liveness and readiness probes
- **Delivery**: Alembic migrations, multi-stage non-root Docker image, CI that runs
  lint, tests (93% coverage), migrations on Postgres, and a full Docker end-to-end test

## Quick start (no Docker needed)

```bash
make install          # venv + dependencies
make dev              # migrations + API on http://localhost:8000 (SQLite, checks run inline)
```

In a second terminal:

```bash
make demo             # creates a demo warehouse with planted defects, 9 rules, and a run
```

Open http://localhost:8000/docs for interactive Swagger docs. Use the API key `dev-api-key`.

## Full stack (Docker)

```bash
docker compose up --build -d
python scripts/seed_demo.py --source-type postgresql \
  --source-url postgresql://dq_reader:dq_reader@postgres:5432/warehouse
```

| Service    | What it does                                    | URL                     |
|------------|-------------------------------------------------|-------------------------|
| api        | FastAPI REST API                                | http://localhost:8000   |
| worker     | Celery worker that executes checks              | metrics on :9100        |
| beat       | Celery beat, which triggers scheduled runs      | n/a                     |
| postgres   | Metadata DB, plus a demo `warehouse` DB         | localhost:5432          |
| redis      | Celery message broker                           | localhost:6379          |
| prometheus | Scrapes API and worker metrics                  | http://localhost:9090   |

## API at a glance

All `/api/v1` routes need an `X-API-Key` header.

| Method | Path                                         | Purpose                               |
|--------|----------------------------------------------|---------------------------------------|
| POST   | `/api/v1/data-sources`                       | Register a database to monitor        |
| GET    | `/api/v1/data-sources`                       | List (paginated)                      |
| PATCH  | `/api/v1/data-sources/{id}`                  | Update schedule, connection, active   |
| POST   | `/api/v1/data-sources/{id}/test-connection`  | Check the source is reachable         |
| POST   | `/api/v1/data-sources/{id}/runs`             | Trigger a run (202, async)            |
| GET    | `/api/v1/rules/types`                        | List supported check types            |
| POST   | `/api/v1/rules`                              | Create a validation rule              |
| GET    | `/api/v1/rules/{id}/results`                 | Result history for one rule           |
| GET    | `/api/v1/runs/{id}`                          | Run status, with every result         |
| GET    | `/api/v1/alerts?status=open&severity=critical` | Filter alerts                       |
| POST   | `/api/v1/alerts/{id}/acknowledge`            | Acknowledge an alert                  |
| POST   | `/api/v1/alerts/{id}/resolve`                | Resolve an alert                      |
| GET    | `/api/v1/dashboard/summary`                  | Health overview across all sources    |
| GET    | `/health`, `/ready`, `/metrics`              | Probes and Prometheus metrics         |

Example: a rule that fails when more than 1% of orders have no customer:

```bash
curl -X POST localhost:8000/api/v1/rules -H 'X-API-Key: dev-api-key' \
  -H 'Content-Type: application/json' -d '{
    "data_source_id": 1, "name": "customer_id present", "rule_type": "not_null",
    "table_name": "orders", "column_name": "customer_id",
    "threshold": 0.01, "severity": "critical"
  }'
```

## Project layout

```
app/
  main.py              FastAPI app factory, router wiring, lifespan
  config.py            Settings from environment variables (pydantic-settings)
  database.py          SQLAlchemy engine/session for the metadata DB
  models.py            ORM models: DataSource, ValidationRule, CheckRun, CheckResult, Alert
  schemas.py           Pydantic request/response models (the API contract)
  observability.py     JSON logging, request IDs, Prometheus metrics
  api/                 Routes + shared dependencies (auth, pagination)
  engine/
    checks.py          Check implementations, compiled to SQL (the core logic)
    connectors.py      Pooled connections to monitored databases
    runner.py          Executes a run: all rules, results, alerts
  services/
    alerting.py        Alert dedup / auto-resolve, webhook notifications
    scheduler.py       Cron evaluation, dispatch of due runs
  worker/              Celery app + tasks (run_checks, notifications, scheduler tick)
alembic/               Database migrations
tests/                 Unit, API and scheduler tests (pytest)
deploy/                Prometheus config, Postgres demo-warehouse seed
scripts/seed_demo.py   Demo data + rules through the public API
```

## Development

```bash
make test     # pytest with coverage
make lint     # ruff lint + format check
make format   # auto-fix
```

New migration after changing `app/models.py`:

```bash
.venv/bin/alembic revision --autogenerate -m "describe change"
```

## Production notes

- Connect each monitored source with a **read-only database user**. The demo creates
  `dq_reader` this way. That is the real defence for `custom_sql`. The keyword filter is
  only a second layer.
- Set a strong `API_KEY`. Store connection URLs in a secrets manager, or encrypt the
  `connection_config` column.
- `statement_timeout` (default 30s) is applied to every Postgres check connection.
