# DQ Monitor

[![CI](../../actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)

A backend service for **data quality monitoring**. You register databases as data sources,
define validation rules (not-null, uniqueness, ranges, freshness, custom SQL, and more),
and DQ Monitor runs them on a schedule with background workers. It records every result
and opens a deduplicated alert, with an optional Slack webhook, when data breaks. When the
data recovers, it resolves the alert on its own.

**Stack:** Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2.0 · PostgreSQL · Alembic ·
Celery · RabbitMQ · Redis · JWT (PyJWT + bcrypt) · Prometheus · Docker Compose · pytest ·
Ruff · GitHub Actions

```
            JWT                       publish                  consume
 client ──────────▶ FastAPI API ──────────────▶ RabbitMQ ─────────────▶ Celery workers ──SQL──▶ monitored DBs
                     │      │                 dq.tasks │ failed after         │
                     │      │                          ▼ retries              │
                     │      │                       dq.dead (DLQ)             │
                     │      └── cache / rate limit ──▶ Redis ◀── invalidate ──┤
                     └────────────────────▶ PostgreSQL ◀──────────────────────┘
                                 (users, sources, rules, runs, results, alerts)
              Celery beat ──▶ RabbitMQ (scheduled runs)          Prometheus ◀── /metrics
```

More documentation:

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): the design, with diagrams.
- [docs/TOOLS.md](docs/TOOLS.md): a learning guide to every tool and where it's used.
- [docs/DQ_Monitor_Requirements.pdf](docs/DQ_Monitor_Requirements.pdf): the requirements spec.

## Features

- **8 check types**, all pushed down to the database as SQL: `not_null`, `unique`, `range`,
  `accepted_values`, `regex`, `row_count`, `freshness`, `custom_sql`.
- **Failure thresholds**: a rule can tolerate a set fraction of bad rows, plus sample bad
  values stored with every failing result.
- **JWT auth with roles**: bcrypt-hashed passwords and short-lived tokens. `viewer`s can
  read; only `editor`s can create, change, delete or trigger runs.
- **Async execution over RabbitMQ**: the API returns `202 Accepted` and Celery workers run
  the checks. Tasks retry with exponential backoff. A task that keeps failing goes to a
  **dead-letter queue** (`dq.dead`) and is never silently dropped. `scripts/dlq.py` lists
  and replays them.
- **Redis cache** for hot reads (per-source status and the dashboard). It's cache-aside with
  invalidation on every write, a TTL as a safety net, and an `X-Cache: HIT|MISS` header.
- **Rate limiting** in Redis: per IP for the API, and stricter for logins.
- **Cron scheduling** through Celery beat, with no pile-up of overlapping runs.
- **Alert lifecycle**: open → acknowledged → resolved, with deduplication and auto-resolve.
  Webhook notifications are retried.
- **One error format** for every failure:
  `{"error": {"code", "message", "details", "request_id"}}`.
- **Observability**: JSON logs with request IDs, Prometheus metrics, liveness and readiness probes.
- **Delivery**: Alembic migrations, a non-root multi-stage Docker image, and **81 tests
  (94% coverage)**. CI runs lint, tests, a Postgres migration round-trip, and a full Docker
  end-to-end test.

## Quick start (no Docker needed)

Locally, checks run inline: no RabbitMQ or Redis is required, and the cache and rate limiter
switch themselves off.

```bash
make install          # venv + dependencies
make dev              # migrations + API on http://localhost:8000 (SQLite)
```

In a second terminal:

```bash
make demo             # creates editor admin@example.com / admin-password, demo data, 9 rules, a run
```

Open http://localhost:8000/docs, click **Authorize**, and log in with
`admin@example.com` / `admin-password`.

## Full stack (Docker)

```bash
docker compose up --build -d
python scripts/seed_demo.py --source-type postgresql \
  --source-url postgresql://dq_reader:dq_reader@postgres:5432/warehouse
```

| Service    | What it does                                     | URL                      |
|------------|--------------------------------------------------|--------------------------|
| api        | FastAPI REST API                                 | http://localhost:8000    |
| worker     | Celery worker (consumes `dq.tasks`)              | metrics on :9100         |
| beat       | Celery beat, which triggers scheduled runs       | n/a                      |
| rabbitmq   | Message broker, plus a management UI             | http://localhost:15672   |
| redis      | Read cache + rate limiter                        | localhost:6379           |
| postgres   | Metadata DB, plus a demo `warehouse` DB          | localhost:5432           |
| prometheus | Scrapes API and worker metrics                   | http://localhost:9090    |

## API at a glance

All `/api/v1` routes except login need `Authorization: Bearer <token>`. Writes need the
`editor` role.

| Method | Path                                           | Who     | Purpose                              |
|--------|------------------------------------------------|---------|--------------------------------------|
| POST   | `/api/v1/auth/login`                           | anyone  | Email + password (form) → JWT        |
| GET    | `/api/v1/auth/me`                              | any     | Current user                         |
| POST/GET/PATCH | `/api/v1/users`                        | editor  | Manage users and roles               |
| POST   | `/api/v1/data-sources`                         | editor  | Register a database to monitor       |
| GET    | `/api/v1/data-sources/{id}/status`             | any     | Latest health (**cached**)           |
| POST   | `/api/v1/data-sources/{id}/runs`               | editor  | Trigger a run (202, async)           |
| POST   | `/api/v1/rules`                                | editor  | Create a validation rule             |
| GET    | `/api/v1/rules/{id}/results`                   | any     | Result history for one rule          |
| GET    | `/api/v1/runs/{id}`                            | any     | Run status, with every result        |
| GET    | `/api/v1/alerts?status=open&severity=critical` | any     | Filter alerts                        |
| POST   | `/api/v1/alerts/{id}/acknowledge` / `resolve`  | editor  | Alert lifecycle                      |
| GET    | `/api/v1/dashboard/summary`                    | any     | Overview (**cached**)                |
| GET    | `/health`, `/ready`, `/metrics`                | public  | Probes and Prometheus                |

Every list endpoint is paginated (`limit` ≤ 200, `offset`) and returns
`{items, total, limit, offset}`.

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login \
  -d 'username=admin@example.com&password=admin-password' | jq -r .access_token)

curl -X POST localhost:8000/api/v1/rules -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{
    "data_source_id": 1, "name": "customer_id present", "rule_type": "not_null",
    "table_name": "orders", "column_name": "customer_id",
    "threshold": 0.01, "severity": "critical"
  }'
```

## Design decisions and trade-offs

**Why a queue instead of FastAPI `BackgroundTasks`?** Background tasks run inside the API
process. If the process restarts or crashes, the work is lost, and heavy checks compete with
request handling for CPU and connections. A broker persists the job, lets workers scale
separately from the API, and provides retries, redelivery and a dead-letter queue.

**What if a worker crashes mid-job?** Messages are acknowledged *after* the task finishes
(`acks_late`), so RabbitMQ redelivers a job whose worker died. A redelivered job could run
twice, so the runner is **idempotent**: it only executes runs still marked `queued`. Infrastructure
errors are retried with backoff, then the message is rejected into `dq.dead` and the run is
marked `failed`. If the broker itself is down when a run is triggered, the API marks the run
`failed` and returns 503, rather than leaving a `queued` row nobody will process.

**Why RabbitMQ rather than Redis as the broker?** RabbitMQ is built for messaging: durable
queues, per-message acks, and native dead-lettering. Redis stays where it's strongest, as
the cache and rate-limit counters.

**How is the cache kept consistent with the DB?** Cache-aside. Reads fill the cache on a
miss. Every write (new run, run finished, alert change, rule or source change) **commits
first, then deletes** the affected keys, so the next read rebuilds from the DB. Deleting
instead of updating can never store a wrong value. A short TTL (60s) bounds staleness if an
invalidation is ever missed, or if a concurrent read re-caches old data. If Redis is down,
the app **fails open** and serves from the DB.

**Why JWT, and its downsides?** Tokens are stateless: any API replica verifies them with the
shared secret, with no session store. The downside is that a token can't be revoked before
it expires. This is mitigated with short expiry (60 min) and by re-loading the user on every
request, so a deactivated user or a changed role takes effect immediately. Other safeguards:
the secret must be at least 32 characters outside local dev; the algorithm is pinned (no
`alg: none`); login errors don't reveal which emails exist; and logins are rate-limited.

**How would this scale to 10,000 datasets checked hourly?** That is about 3 runs per second
on average. Run more workers, which are stateless (`--concurrency`, replicas). Pushdown keeps
each check cheap, because the database does the counting. Use per-source routing or separate
queues so a slow warehouse can't starve the others, and spread schedules (jitter) to avoid a
top-of-hour spike. Partition `check_results` by month, add a retention job, and add read
replicas for the dashboard. The cache already absorbs repeated status reads.

## Project layout

```
app/
  main.py              App factory, router wiring, startup checks, first-user bootstrap
  config.py            Settings from environment variables (pydantic-settings)
  security.py          bcrypt password hashing, JWT create/verify
  cache.py             Redis cache-aside helpers, invalidation, rate limiter
  database.py          SQLAlchemy engine/session for the metadata DB
  models.py            ORM: User, DataSource, ValidationRule, CheckRun, CheckResult, Alert
  schemas.py           Pydantic request/response models (the API contract)
  observability.py     JSON logging, request IDs, Prometheus metrics
  cli.py               `python -m app.cli create-user ...`
  api/
    deps.py            Auth, role enforcement, rate limiting, pagination
    errors.py          The single error-response format
    routes/            auth, data_sources, rules, runs, alerts (+ dashboard), health
  engine/              checks.py (SQL check types), connectors.py, runner.py
  services/            alerting.py (dedup, webhooks), scheduler.py (cron)
  worker/              celery_app.py (RabbitMQ queues + DLQ), tasks.py
alembic/               Migrations (0001 schema, 0002 users)
tests/                 81 tests: checks, API, auth, cache, errors, worker, scheduler
scripts/               seed_demo.py, dlq.py (list/replay dead letters)
deploy/                Prometheus config, Postgres demo warehouse
```

## Development

```bash
make test     # pytest with coverage
make lint     # ruff lint + format check
make user     # create the local demo editor
make dlq      # list dead-lettered tasks (needs RabbitMQ)
```

New migration after changing `app/models.py`:

```bash
.venv/bin/alembic revision --autogenerate -m "describe change"
```

## Production notes

- Set `JWT_SECRET` to a random value of at least 32 characters. The app refuses to start
  without one outside local dev.
- Connect each monitored source with a **read-only database user**. The demo creates
  `dq_reader` this way.
- Store connection URLs in a secrets manager, or encrypt the `connection_config` column.
- Watch the depth of `dq.dead` (RabbitMQ UI or metrics). A growing dead-letter queue means
  something is persistently broken.
