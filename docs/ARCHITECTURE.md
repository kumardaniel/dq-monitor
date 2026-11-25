# DQ Monitor: Architecture

## 1. System context

```mermaid
flowchart LR
    user([API client / UI / curl]) -->|HTTPS + Bearer JWT| api[FastAPI API]
    api -->|read/write| meta[(PostgreSQL<br/>metadata DB)]
    api <-->|cache + rate limit| redis[(Redis)]
    api -->|publish run_checks| mq[[RabbitMQ<br/>dq.tasks]]
    beat[Celery beat] -->|every N s| mq
    mq --> worker[Celery workers]
    mq -.->|rejected after retries| dlq[[RabbitMQ<br/>dq.dead]]
    worker -->|results, alerts| meta
    worker -->|invalidate keys| redis
    worker -->|SQL checks, read-only| src1[(Monitored DB 1)]
    worker -->|SQL checks, read-only| src2[(Monitored DB 2)]
    worker -->|POST JSON| hook[Slack / webhook]
    prom[Prometheus] -->|scrape /metrics| api
    prom -->|scrape :9100| worker
```

There are three kinds of process, all built from **one Docker image**:

| Process | Role | Scales by |
|---|---|---|
| **API** (uvicorn + FastAPI) | Auth, CRUD, triggers runs, serves cached reads | More replicas behind a load balancer (stateless: JWT + shared Redis) |
| **Worker** (Celery) | Executes checks, records results, manages alerts, sends notifications | More workers or higher `--concurrency` |
| **Beat** (Celery beat) | Clock: every tick, asks "which sources are due?" | Exactly one instance |

Each store has one job:

- **PostgreSQL** is the system of record.
- **RabbitMQ** carries jobs, durably and with acknowledgements.
- **Redis** holds only *derived* data: cached reads and rate-limit counters. Losing Redis
  loses nothing; the app serves from Postgres and rebuilds the cache.

## 2. Request flow: triggering a run

```mermaid
sequenceDiagram
    participant C as Client
    participant A as API
    participant DB as Metadata DB
    participant R as RabbitMQ
    participant W as Worker
    participant S as Monitored DB
    participant H as Webhook

    C->>A: POST /data-sources/1/runs (Bearer JWT)
    A->>A: verify JWT, load user, require editor, rate limit
    A->>DB: any run queued/running? (409 if yes)
    A->>DB: INSERT check_runs (status=queued)
    A->>DB: COMMIT
    A->>R: publish run_checks(run_id) to dq.tasks (503 + run failed if broker down)
    A-->>C: 202 Accepted {id, status: queued}
    R->>W: deliver task
    W->>DB: status=queued? then mark running (idempotency)
    loop each active rule
        W->>S: SELECT count(*) ... / failure condition
        S-->>W: totals, failing rows, samples
        W->>DB: INSERT check_results
        W->>DB: open / dedupe / resolve alert
    end
    W->>DB: run status=completed + counts, COMMIT
    W->>W: delete cached status + summary in Redis
    W->>R: send_alert_notification.delay(...) for new and resolved alerts
    R->>W: deliver
    W->>H: POST {"text": "..."} (retries with backoff; then dq.dead)
    C->>A: GET /runs/{id}
    A-->>C: run + results
```

**Why commit before enqueue?** If the task were enqueued first, a fast worker could pick it
up before the API's transaction commits. The worker would then find no run row.

## 3. Data model

```mermaid
erDiagram
    DATA_SOURCES ||--o{ VALIDATION_RULES : has
    DATA_SOURCES ||--o{ CHECK_RUNS : has
    CHECK_RUNS ||--o{ CHECK_RESULTS : produces
    VALIDATION_RULES ||--o{ CHECK_RESULTS : evaluated_in
    VALIDATION_RULES ||--o{ ALERTS : raises
    CHECK_RESULTS |o--o{ ALERTS : last_result

    USERS {
        int id PK
        string email UK
        string hashed_password
        string role
        bool is_active
    }

    DATA_SOURCES {
        int id PK
        string name UK
        string source_type
        json connection_config
        string schedule_cron
        bool is_active
        timestamptz last_scheduled_at
    }
    VALIDATION_RULES {
        int id PK
        int data_source_id FK
        string rule_type
        string table_name
        string column_name
        json rule_config
        string severity
        float threshold
        bool is_active
    }
    CHECK_RUNS {
        int id PK
        int data_source_id FK
        string status
        string trigger
        int passed_rules
        int failed_rules
        int error_rules
    }
    CHECK_RESULTS {
        int id PK
        int run_id FK
        int rule_id FK
        string status
        int total_rows
        int failing_rows
        float observed_value
        json sample_failures
        float duration_ms
    }
    ALERTS {
        int id PK
        int rule_id FK
        string severity
        string status
        int occurrences
    }
```

Status values:

- `check_runs.status`: `queued → running → completed | failed`
- `check_results.status`: `passed | failed | error`. `error` means the check could not run,
  for example a missing column or an unreachable database.
- `alerts.status`: `open → acknowledged → resolved`

Foreign keys use `ON DELETE CASCADE`: deleting a source removes its rules, runs, results and
alerts. Columns used in filters (`status`, `data_source_id`, `rule_id`) are indexed.

## 4. The check engine (`app/engine/checks.py`)

Each check type is a small class registered in `CHECKS`. Most share one pattern
(`RowLevelCheck`): *count all rows, count rows matching a failure condition, then compare
the ratio with the threshold.*

| rule_type | Failure condition (SQL) | Config |
|---|---|---|
| `not_null` | `col IS NULL` | none |
| `range` | `col < min OR col > max` | `min`, `max` |
| `accepted_values` | `col NOT IN (...)` | `values` |
| `regex` | `NOT col ~ pattern` | `pattern` |
| `unique` | `count(col) - count(DISTINCT col)` | none |
| `row_count` | `count(*)` outside `[min, max]` | `min`, `max` |
| `freshness` | `now() - max(col) > max_age` | `max_age_minutes` |
| `custom_sql` | the query returns any rows | `sql` |

**Pushdown:** the database does the counting. A 100M-row table costs DQ Monitor two
`count(*)` queries, not 100M rows over the network.

**Safety layers:**

1. Table and column names must match `^[A-Za-z_][A-Za-z0-9_]*$`. They are validated when
   the rule is created *and* again when it runs.
2. Queries are built with SQLAlchemy Core, so identifiers are quoted per dialect and values
   are bound parameters. There is no string concatenation of user input.
3. `custom_sql` must be a single `SELECT`/`WITH` statement with no DDL or DML keywords. On
   Postgres it runs in `SET TRANSACTION READ ONLY`.
4. Every check connection is rolled back, never committed.
5. Postgres connections get `statement_timeout` so a runaway query is cancelled.
6. Recommended: a read-only DB user per source.

Adding a check type takes one subclass and one entry in the registry. The API, validation
and `/rules/types` pick it up automatically.

## 5. Reliability design

| Concern | How it's handled |
|---|---|
| Worker crashes mid-run | `task_acks_late` + `reject_on_worker_lost`: RabbitMQ redelivers the message |
| Duplicate delivery | Runner only executes runs in `queued`; a second delivery is a no-op |
| Metadata DB blip during a run | Task retries with exponential backoff (5s, 10s, 20s); the run is reset to `queued` before each retry |
| Task keeps failing | `task_acks_on_failure_or_timeout=False`: the final failure *rejects* the message, so RabbitMQ dead-letters it to `dq.dead` and the run is marked `failed` |
| Broker down when triggering | API marks the run `failed` and returns 503, so no orphaned `queued` row is left |
| Inspecting / recovering dead letters | `scripts/dlq.py list` (non-destructive) and `replay` (re-queues the run row, resets retries) |
| One rule breaks | Caught per rule and recorded as `error`; the other rules still run |
| Overlapping scheduled runs | The scheduler skips a source with a run still `queued`/`running` |
| Multiple schedulers | `SELECT ... FOR UPDATE SKIP LOCKED` on the source rows |
| Webhook down | 5 retries with exponential backoff and jitter, then dead-lettered |
| Alert storms | One active alert per rule; repeats increment `occurrences` |
| Redis down | Cache and rate limiter **fail open**; the API keeps serving from Postgres |
| Slow source | `statement_timeout`; worker concurrency isolates sources |

## 6. Authentication and authorization

```mermaid
sequenceDiagram
    participant C as Client
    participant A as API
    participant DB as PostgreSQL
    C->>A: POST /auth/login (email, password) [rate-limited per IP+email]
    A->>DB: SELECT user by email
    A->>A: bcrypt.checkpw (dummy hash if no user: same timing)
    A-->>C: {access_token: JWT(sub, role, exp=60m)}
    C->>A: GET /rules (Authorization: Bearer JWT)
    A->>A: verify signature + exp, algorithm pinned to HS256
    A->>DB: load user (must exist and be active; role read from DB)
    A-->>C: 200 / 401 / 403
```

- **Passwords**: bcrypt with a per-password salt and a work factor of 12. Passwords must be
  8 to 72 bytes. The plain password is never stored or logged.
- **Roles**: `viewer` (read) and `editor` (write). Enforced **once per router** by
  `enforce_role_by_method`, so any non-GET needs an editor. A new write endpoint can't ship
  without the check by mistake.
- **Revocation trade-off**: JWTs can't be revoked early. The user is re-checked on every
  request, so deactivation and role changes take effect immediately. That costs one primary-key
  lookup per request. A token denylist in Redis would be the next step if instant revocation
  of *tokens* (not users) were needed.
- **First user**: `BOOTSTRAP_EDITOR_EMAIL/PASSWORD` creates an editor only into an empty
  table. Otherwise use `python -m app.cli create-user`.

## 7. Caching and rate limiting (Redis)

| Key | Contents | Invalidated by |
|---|---|---|
| `dq:cache:source_status:{id}` | Latest run, health, open alert counts for one source | run queued / started / finished / failed; alert ack/resolve; rule or source change |
| `dq:cache:summary` | Dashboard aggregates across all sources | the same events, for any source |
| `dq:ratelimit:ip:{ip}:{minute}` | Request counter (fixed window, `INCR` + `EXPIRE`) | expires after the window |
| `dq:ratelimit:login:{ip}:{email}:{minute}` | Login attempt counter | expires after the window |

The order matters: **commit to Postgres, then delete the key.** Deleting first opens a
window where a concurrent read re-caches the old state from the not-yet-committed DB. A
narrower race remains: a read computes old data, an invalidation runs, then the read writes
its stale value. The TTL (60s) bounds that case. Responses carry `X-Cache: HIT|MISS`, which
makes the behaviour visible and testable.

## 8. Error format

Every non-2xx response, whether it comes from validation, auth, not-found, a conflict, a rate
limit or an unexpected crash, has the same body:

```json
{"error": {"code": "validation_error", "message": "Request validation failed",
           "details": [{"field": "body.threshold", "message": "..."}],
           "request_id": "9f2c..."}}
```

`code` is stable for programs to branch on. `request_id` matches the `X-Request-ID` header
and the server logs. A 500 never includes internals such as stack traces or SQL.

## 9. Observability

- **Logs:** one JSON object per line, with `request_id` on API logs. Clients may send
  `X-Request-ID` and it is echoed back.
- **Metrics** (Prometheus):
  - `dq_http_requests_total{method,route,status}`, `dq_http_request_duration_seconds`
  - `dq_check_results_total{rule_type,status}`, `dq_check_duration_seconds{rule_type}`
  - `dq_runs_total{status,trigger}`, `dq_alerts_opened_total{severity}`,
    `dq_notifications_total{outcome}`
  - `dq_cache_requests_total{result=hit|miss}`, `dq_rate_limited_total{scope}`
  - HTTP metrics are labelled by the route *template* (`/runs/{run_id}`), which keeps label
    cardinality bounded.
- **Probes:** `/health` (process alive) and `/ready` (DB reachable, else 503). This matches
  Kubernetes liveness and readiness semantics.

## 10. Configuration and environments

All settings live in `app/config.py` and are read from environment variables.

| Environment | Metadata DB | Execution |
|---|---|---|
| Local dev | SQLite file | Celery eager mode: tasks run inline, no RabbitMQ; Redis optional (off = no cache) |
| Tests | Temp SQLite | Eager mode + in-memory fakeredis, so assertions run right after the HTTP call |
| Docker / prod | PostgreSQL | Celery workers on RabbitMQ, Redis cache + rate limiting |

Code paths stay the same in every environment. Only the configuration changes.

## 11. Trade-offs and future work

- **Roles are global** (viewer/editor). Per-team ownership of sources would need a
  `team_id` on sources and scoped queries. Refresh tokens would allow shorter access tokens.
- **Connection URLs are stored in plain JSON.** Production should encrypt them, for example
  with Fernet or a KMS, or reference a secrets manager.
- **Synchronous SQLAlchemy** in FastAPI routes: each request runs in FastAPI's threadpool.
  That is simpler than async SQLAlchemy and fine at this scale, because the heavy work
  happens on workers.
- **Result retention**: results grow without limit. A periodic purge task or monthly table
  partitions would cap that.
- **More connectors**: Snowflake, BigQuery and MySQL need only a SQLAlchemy dialect and an
  entry in `SUPPORTED_SOURCE_TYPES`.
- **Anomaly detection**: `observed_value` history per rule already supports
  z-score/seasonal checks (for example, "row count dropped 40% vs. 7-day average").
