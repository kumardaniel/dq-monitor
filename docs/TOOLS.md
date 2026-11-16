# Tools guide: what each one is, why it's here, and where to look

This guide is for learning the project tool by tool. For each tool you get: what it is,
why this project uses it, the exact files to read, and an exercise to try.

**Suggested order:** Python typing → FastAPI → Pydantic → SQLAlchemy → Alembic →
PostgreSQL → pytest → Celery + Redis → Docker → Prometheus → GitHub Actions.

---

## 1. FastAPI: the web framework
**What:** A Python framework for building HTTP APIs. It reads your type hints to validate
requests, serialize responses, and generate OpenAPI/Swagger docs.
**Why here:** Every client interaction (create rules, trigger runs, read alerts) is a REST call.
**Read:**
- `app/main.py`: the app factory, `include_router`, and router-level `dependencies=` (API-key auth on every `/api/v1` route).
- `app/api/routes/*.py`: endpoints. Note `status_code=201/202/204`, `response_model=`, and `HTTPException(404/409)`.
- `app/api/deps.py`: **dependency injection**. `DB` gives each request a session, `Paging` parses `?limit&offset`, `require_api_key` does auth.
- `app/observability.py`: **middleware** (`RequestContextMiddleware`) that wraps every request.

**Concepts to learn:** path vs. query params, dependency injection, `Annotated`,
response models, status codes (201 Created, 202 Accepted, 204 No Content, 401, 404, 409, 422),
the lifespan event, middleware.
**Exercise:** Add `GET /api/v1/rules/{id}/latest` that returns the newest result for a rule.

## 2. Pydantic v2: validation and serialization
**What:** Defines data shapes as classes and validates input against them.
**Why here:** Bad input (unknown rule type, invalid cron, missing `min`/`max`) is rejected
with a 422 before it reaches the DB.
**Read:** `app/schemas.py`
- `Field(ge=0, le=1)` for numeric limits, and `Literal["warning","critical"]` for enums.
- `@field_validator` (one field) vs. `@model_validator(mode="after")` (cross-field: the
  rule config depends on rule_type).
- `ConfigDict(from_attributes=True)` builds a response from an ORM object.
- `DataSourceOut._mask` transforms output, which keeps passwords out of responses.
- `Page[T]`: a **generic** response model reused by every list endpoint.
- `app/config.py`: **pydantic-settings** loads typed config from env vars.

**Exercise:** Add a `description: str | None` field (max 500 chars) to rules.

## 3. SQLAlchemy 2.0: ORM and SQL toolkit
**What:** Maps Python classes to tables (ORM) and builds SQL from Python (Core).
**Why here:** It is used two ways. The ORM handles the metadata DB. **Core** builds the
check queries portably and safely.
**Read:**
- `app/models.py`: typed models (`Mapped[...]`, `mapped_column`), relationships, `cascade`, `ondelete`.
- `app/database.py`: engine, `sessionmaker`, the session-per-request pattern.
- `app/engine/checks.py`: Core: `table()`, `column()`, `select(func.count())`, `.where()`,
  `group_by().having()`, `regexp_match`, `text().columns().subquery()`.
- `app/api/routes/runs.py`: `selectinload` avoids the **N+1 query problem**.
- `app/api/routes/alerts.py` (`summary`): aggregates with `GROUP BY`, `case()`, and a
  "latest row per group" subquery.
- `app/services/scheduler.py`: `with_for_update(skip_locked=True)`.

**Concepts:** session/unit of work, `flush` vs. `commit`, lazy vs. eager loading, connection
pooling (`pool_pre_ping`), transactions, bound parameters (SQL injection prevention).
**Exercise:** Set `echo=True` on the engine in `app/database.py`, run the tests, and read
the SQL each check sends.

## 4. Alembic: database migrations
**What:** Version control for your database schema.
**Why here:** Production databases can't be dropped and recreated. Schema changes are
applied as ordered, reviewable scripts.
**Read:** `alembic/env.py` (reads `DATABASE_URL` from settings, `render_as_batch` for SQLite)
and `alembic/versions/0001_initial_schema.py`.
**Commands:**
```bash
alembic upgrade head                                 # apply all migrations
alembic revision --autogenerate -m "add x column"    # diff models vs DB into a new script
alembic downgrade -1                                 # undo the last one
```
**Exercise:** Add the `description` column from the Pydantic exercise, autogenerate a
migration, and read the generated file.

## 5. PostgreSQL: the production database
**What:** A relational database that is the industry default for backends.
**Why here:** It stores the metadata (sources, rules, runs, results, alerts), and it is also
a common kind of *monitored* source.
**Where:** `docker-compose.yml` (service), `deploy/postgres-init/01_warehouse.sql` (demo DB,
`generate_series`, a read-only `GRANT SELECT` user).
**Concepts used:** indexes, foreign keys with cascade, `statement_timeout`, read-only
transactions, `FOR UPDATE SKIP LOCKED`, `TIMESTAMPTZ`.
**Exercise:** `docker compose exec postgres psql -U dq -d dq_monitor`, then run
`EXPLAIN ANALYZE SELECT * FROM check_results WHERE rule_id = 1;`.

## 6. Celery + Redis: background jobs
**What:** Celery is a distributed task queue. Redis is the message broker that carries task
messages from producers (API, beat) to workers.
**Why here:** Checks can take seconds to minutes. The API shouldn't block, so it enqueues
and returns `202`. Workers scale on their own, and beat provides cron scheduling.
**Read:**
- `app/worker/celery_app.py`: config. **Read the comments on `task_acks_late`** and the `beat_schedule`.
- `app/worker/tasks.py`: three tasks, including `autoretry_for` + `retry_backoff` on notifications.
- `app/engine/runner.py`: the **idempotency** check (`status != "queued"` → skip).
- `app/services/scheduler.py`: how beat decides what's due (with `croniter`).

**Concepts:** producer/consumer, at-least-once delivery, idempotency, ack-late, retries with
exponential backoff and jitter, eager mode for tests.
**Exercise:** Run `make up`, then `docker compose logs -f worker`, trigger a run, and watch
it move through the worker.

## 7. httpx: outgoing HTTP
**What:** An HTTP client, a modern alternative to `requests`.
**Why here:** Sends alert webhooks (`app/services/alerting.py::send_webhook`) and powers the
demo script. Note the explicit `timeout`. Never make a network call without one.

## 8. pytest: testing
**What:** Python's standard test framework.
**Why here:** 48 tests cover check logic, the API end to end, alerting, and scheduling, at
93% coverage.
**Read:**
- `tests/conftest.py`: **fixtures** (temp DBs, `TestClient`, factory fixture `make_rule`),
  plus a seeded "warehouse" with exactly known defects.
- `tests/test_checks.py`: `@pytest.mark.parametrize` covering many invalid configs in one test.
- `tests/test_api.py`: `unittest.mock.patch` fakes the webhook. `monkeypatch` changes settings.
- `tests/test_scheduler.py`: time is passed in as `now=`, so tests are deterministic.

**Commands:** `make test`, `pytest -k alert -v`, `pytest --cov --cov-report=html`.
**Exercise:** Write a test for `PATCH /data-sources/{id}` with an invalid connection URL.

## 9. Ruff: lint and format
**What:** A very fast linter and formatter that replaces flake8, isort and black.
**Config:** `[tool.ruff]` in `pyproject.toml`. Run `make lint` / `make format`.

## 10. Docker and Docker Compose: packaging and local infra
**What:** Docker packages the app and its dependencies into an image. Compose runs a
multi-container stack.
**Read:**
- `Dockerfile`: a **multi-stage build** (wheels built in stage 1, slim runtime in stage 2),
  **non-root user**, `HEALTHCHECK`.
- `docker-compose.yml`: one image runs 4 roles (migrate, api, worker, beat). There are
  `depends_on` health conditions, a one-shot `migrate` job, YAML anchors (`x-app: &app`), and volumes.

**Exercise:** `docker compose up --build`, then `docker compose ps`, then
`docker compose stop worker`. Trigger a run and see it stay `queued`. Start the worker
again and watch it get processed.

## 11. Prometheus: metrics
**What:** A time-series database that *scrapes* `/metrics` endpoints.
**Read:** `app/observability.py` (Counter vs. Histogram, labels), `app/api/routes/health.py`
(`/metrics`), and `deploy/prometheus.yml`.
**Exercise:** At http://localhost:9090, query
`sum by (status) (rate(dq_check_results_total[5m]))` and
`histogram_quantile(0.95, rate(dq_http_request_duration_seconds_bucket[5m]))`.

## 12. GitHub Actions: CI
**What:** Runs workflows on every push and PR.
**Read:** `.github/workflows/ci.yml`. It has four jobs:
1. `lint` runs ruff.
2. `test` runs pytest, with a coverage floor of 85%.
3. `migrations-postgres` applies, rolls back and reapplies migrations against a real
   Postgres **service container**.
4. `e2e-docker` boots the whole stack, seeds data, and checks that a run completes on a real worker.

---

## Interview cheat sheet: questions this project lets you answer

- **"Walk me through a request."** Use the sequence diagram in `ARCHITECTURE.md` §2.
- **"Why a queue instead of running checks in the API?"** Latency, isolation, independent
  scaling, retries, and scheduling.
- **"What if a worker dies mid-task?"** Ack-late redelivery plus an idempotent runner.
- **"How do you prevent SQL injection when users define queries?"** Identifier allowlist
  regex, SQLAlchemy Core quoting, bound parameters, read-only transactions, and a read-only
  DB user.
- **"How did you avoid alert fatigue?"** One active alert per rule, an occurrence counter,
  notifying only on open and resolve, and auto-resolve.
- **"How do you scale this to 10k rules?"** Add workers, use per-source queues or routing,
  pushdown keeps each check cheap, partition `check_results` by month, and add a retention job.
- **"How would you test time-dependent code?"** Inject `now` (see `dispatch_due_runs`).
- **"What's N+1 and where did you avoid it?"** `selectinload` in `GET /runs/{id}`.
- **"How do you change the schema safely?"** Alembic migrations, tested up, down and up again in CI.
- **"How do you know it's healthy in prod?"** Readiness probe, Prometheus metrics, and JSON
  logs with request IDs.
