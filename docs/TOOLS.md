# Tools guide: what each one is, why it's here, and where to look

This guide is for learning the project tool by tool. For each tool you get: what it is,
why this project uses it, the exact files to read, and an exercise to try.

**Suggested order:** Python typing → FastAPI → Pydantic → SQLAlchemy → Alembic →
PostgreSQL → JWT + bcrypt → pytest → Celery + RabbitMQ → Redis → Docker → Prometheus →
GitHub Actions.

---

## 1. FastAPI: the web framework
**What:** A Python framework for building HTTP APIs. It reads your type hints to validate
requests, serialize responses, and generate OpenAPI/Swagger docs.
**Why here:** Every client interaction (create rules, trigger runs, read alerts) is a REST call.
**Read:**
- `app/main.py`: the app factory, `include_router`, and router-level `dependencies=` (rate limiting and auth + role checks on every `/api/v1` router), plus exception handlers.
- `app/api/routes/*.py`: endpoints. Note `status_code=201/202/204`, `response_model=`, and `HTTPException(404/409)`.
- `app/api/deps.py`: **dependency injection**. `DB` gives each request a session, `Paging` parses `?limit&offset`, `get_current_user` turns a JWT into a `User`.
- `app/api/errors.py`: **exception handlers** that give every error the same JSON shape.
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

## 6. JWT + bcrypt: authentication
**What:** bcrypt is a deliberately slow, salted password hash. A JWT (JSON Web Token) is a
signed token, `header.payload.signature`, that proves who the caller is without a server-side session.
**Why here:** Users log in once and send `Authorization: Bearer <token>` on every request.
Viewers can read; editors can write.
**Read:**
- `app/security.py`: `hash_password`, `verify_password` (note the dummy hash used to
  equalise timing), `create_access_token` (claims `sub`, `role`, `exp`, `iat`), and
  `decode_access_token` (the **algorithm whitelist**).
- `app/api/deps.py`: `OAuth2PasswordBearer`, `get_current_user` (re-loads the user on every
  request), and `enforce_role_by_method` (one rule for a whole router).
- `app/api/routes/auth.py`: the login (OAuth2 password flow), `/auth/me`, and user management.
- `tests/test_auth.py`: expired, forged and `alg: none` tokens, a deactivated user, viewer
  vs. editor, and login rate limiting.

**Concepts:** authentication vs. authorization, 401 vs. 403, stateless tokens and why they
can't be revoked, salting, work factors, user-enumeration attacks, and timing attacks.
**Exercise:** Paste a token from `/auth/login` into https://jwt.io. The payload is readable
(it is only *signed*, not encrypted), so never put secrets in it.

## 7. Celery + RabbitMQ: background jobs and the dead-letter queue
**What:** Celery is a distributed task queue. RabbitMQ is the message broker. It stores task
messages durably until a worker acknowledges them.
**Why here:** Checks can take seconds to minutes. The API publishes a job and returns `202`.
Workers scale on their own, failures retry, and jobs that keep failing are parked in a
dead-letter queue instead of vanishing.
**Read:**
- `app/worker/celery_app.py`: the **exchange → queue → worker** topology, the
  `x-dead-letter-exchange` queue argument, and the comments on `task_acks_late` and
  `task_acks_on_failure_or_timeout=False`.
- `app/worker/tasks.py`: `run_checks` retries with backoff, re-queues the run row between
  attempts, then raises, which dead-letters the message. Notifications use `autoretry_for`.
- `app/engine/runner.py`: the **idempotency** check (`status != "queued"` → skip).
- `app/api/routes/data_sources.py` (`trigger_run`): commit, then publish, plus the
  broker-down → 503 path.
- `scripts/dlq.py`: reads `dq.dead`, puts messages back (`list`), or republishes them (`replay`).
- `tests/test_worker.py`: retries, recovery, duplicate delivery, and a broker outage.

**Concepts:** producer/consumer, exchanges and routing keys, ack/nack/reject, at-least-once
delivery, idempotency, exponential backoff with jitter, dead-lettering, and poison messages.
**Exercise:** Run `make up`, open http://localhost:15672 (guest/guest), and watch `dq.tasks`
while you trigger runs. Then set `ALERT_WEBHOOK_URL=http://nowhere.invalid` and restart. Fail
a rule, wait about a minute, and see the notifications arrive in `dq.dead`. Finally, run
`docker compose exec worker python scripts/dlq.py list`.

## 8. Redis: caching and rate limiting
**What:** An in-memory key-value store with expiring keys and atomic counters.
**Why here:** Status and dashboard reads are frequent, and computing them costs several
aggregate queries. Redis serves repeats from memory and counts requests for rate limiting
across all API replicas.
**Read:**
- `app/cache.py`: `get_or_set` (cache-aside), `invalidate_source`, `hit_rate_limit`
  (`INCR` + `EXPIRE` in a pipeline), and the **fail-open** error handling.
- Every `cache.invalidate_source(...)` call (`grep -rn invalidate_source app`). Note that each
  one comes *after* `db.commit()`.
- `tests/test_cache_and_errors.py`: hit, then miss after a write, Redis down, and rate limits.

**Concepts:** cache-aside vs. write-through, invalidation vs. TTL, cache stampede, stale
reads and the race behind them, and fixed-window vs. sliding-window rate limiting.
**Exercise:** Run `redis-cli monitor` in one terminal. Call
`GET /api/v1/data-sources/1/status` twice, then trigger a run, and watch the `GET`, `SET`
and `DEL` commands go by.

## 9. httpx: outgoing HTTP
**What:** An HTTP client, a modern alternative to `requests`.
**Why here:** Sends alert webhooks (`app/services/alerting.py::send_webhook`) and powers the
demo script. Note the explicit `timeout`. Never make a network call without one.

## 10. pytest: testing
**What:** Python's standard test framework.
**Why here:** 81 tests cover check logic, the API end to end, auth, caching, errors, the
queue and scheduling, at 94% coverage.
**Read:**
- `tests/conftest.py`: **fixtures** (temp DBs, `TestClient`s logged in as editor or viewer,
  an in-memory **fakeredis**, the factory fixture `make_rule`), plus a seeded "warehouse"
  with exactly known defects.
- `tests/test_checks.py`: `@pytest.mark.parametrize` covering many invalid configs in one test.
- `tests/test_api.py`: `unittest.mock.patch` fakes the webhook. `monkeypatch` changes settings.
- `tests/test_scheduler.py`: time is passed in as `now=`, so tests are deterministic.

**Commands:** `make test`, `pytest -k alert -v`, `pytest --cov --cov-report=html`.
**Exercise:** Write a test for `PATCH /data-sources/{id}` with an invalid connection URL.

## 11. Ruff: lint and format
**What:** A very fast linter and formatter that replaces flake8, isort and black.
**Config:** `[tool.ruff]` in `pyproject.toml`. Run `make lint` / `make format`.

## 12. Docker and Docker Compose: packaging and local infra
**What:** Docker packages the app and its dependencies into an image. Compose runs a
multi-container stack.
**Read:**
- `Dockerfile`: a **multi-stage build** (wheels built in stage 1, slim runtime in stage 2),
  **non-root user**, `HEALTHCHECK`.
- `docker-compose.yml`: one image runs 4 roles (migrate, api, worker, beat), alongside
  Postgres, RabbitMQ, Redis and Prometheus. There are
  `depends_on` health conditions, a one-shot `migrate` job, YAML anchors (`x-app: &app`), and volumes.

**Exercise:** `docker compose up --build`, then `docker compose ps`, then
`docker compose stop worker`. Trigger a run and see it stay `queued`. Start the worker
again and watch it get processed.

## 13. Prometheus: metrics
**What:** A time-series database that *scrapes* `/metrics` endpoints.
**Read:** `app/observability.py` (Counter vs. Histogram, labels), `app/api/routes/health.py`
(`/metrics`), and `deploy/prometheus.yml`.
**Exercise:** At http://localhost:9090, query
`sum by (status) (rate(dq_check_results_total[5m]))` and
`histogram_quantile(0.95, rate(dq_http_request_duration_seconds_bucket[5m]))`.

## 14. GitHub Actions: CI
**What:** Runs workflows on every push and PR.
**Read:** `.github/workflows/ci.yml`. It has four jobs:
1. `lint` runs ruff.
2. `test` runs pytest, with a coverage floor of 85%.
3. `migrations-postgres` applies, rolls back and reapplies migrations against a real
   Postgres **service container**.
4. `e2e-docker` boots the whole stack (Postgres, RabbitMQ, Redis, API, worker, beat), logs
   in, seeds data, checks that a run completes on a real worker, and checks that the
   dead-letter queue exists.

---

## Interview cheat sheet: questions this project lets you answer

- **"Walk me through a request."** Use the sequence diagram in `ARCHITECTURE.md` §2.
- **"Why a queue instead of background tasks inside FastAPI?"** Background tasks die with the
  API process and compete with requests. A broker persists jobs, scales workers on their own,
  and gives retries and a dead-letter queue. (README, *Design decisions*.)
- **"What if a worker dies mid-task?"** Ack-late redelivery plus an idempotent runner. If it
  keeps failing: retries with backoff, then `dq.dead`, with the run marked `failed`.
- **"How do you keep the cache consistent with the DB?"** Cache-aside: commit, then delete
  the key, with a TTL as the backstop. Explain why the order matters and the race the TTL covers.
- **"Why JWT, and what are its downsides?"** Stateless and easy to scale, but it can't be
  revoked early. Short expiry plus a per-request user check covers that. Also mention
  algorithm pinning and the secret-strength check.
- **"401 vs. 403?"** 401: we don't know who you are. 403: we know, and you aren't allowed.
- **"How do you prevent SQL injection when users define queries?"** Identifier allowlist
  regex, SQLAlchemy Core quoting, bound parameters, read-only transactions, and a read-only
  DB user.
- **"How did you avoid alert fatigue?"** One active alert per rule, an occurrence counter,
  notifying only on open and resolve, and auto-resolve.
- **"How would this scale to 10,000 datasets checked every hour?"** About 3 runs per second.
  Add stateless workers, route per source so slow warehouses don't starve the others, jitter
  the schedules, rely on pushdown to keep each check cheap, partition `check_results` and add
  retention, and let the cache absorb dashboard reads.
- **"How would you test time-dependent code?"** Inject `now` (see `dispatch_due_runs`).
- **"What's N+1 and where did you avoid it?"** `selectinload` in `GET /runs/{id}`.
- **"How do you change the schema safely?"** Alembic migrations, tested up, down and up again in CI.
- **"How do you know it's healthy in prod?"** Readiness probe, Prometheus metrics, and JSON
  logs with request IDs.
