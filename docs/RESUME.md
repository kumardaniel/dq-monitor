# Resume entry

**DQ Monitor: Data Quality Monitoring Service** · Python, FastAPI, PostgreSQL, SQLAlchemy,
Celery, Redis, Docker, Prometheus, GitHub Actions · [github.com/&lt;you&gt;/dq-monitor](#)

- Built a REST backend (FastAPI, SQLAlchemy 2.0, PostgreSQL) that runs 8 types of data quality
  checks against external databases: null, uniqueness, range, regex, freshness, row count and
  custom SQL. Checks are compiled to SQL and pushed down to the database, so cost stays flat
  as tables grow.
- Moved check execution to Celery workers over Redis, with cron scheduling through Celery
  beat. Used ack-late delivery, idempotent run processing and `SKIP LOCKED` dispatch, so runs
  are never lost or duplicated when a worker crashes.
- Designed an alert lifecycle with deduplication and auto-resolve, which cut repeat
  notifications to one per incident. Slack-compatible webhooks retry with exponential backoff.
- Hardened user-defined SQL with identifier allowlisting, bound parameters, read-only
  transactions and statement timeouts. Masked credentials in all API responses.
- Added observability: structured JSON logs with request IDs, Prometheus metrics for the API
  and workers, and liveness and readiness probes.
- Shipped with Alembic migrations, a multi-stage non-root Docker image, and a Docker Compose
  stack. 48 pytest tests cover 93% of the code. GitHub Actions CI runs lint, tests, Postgres
  migration round-trips and a full-stack end-to-end test.

Short version (2 lines):

- Built a data quality monitoring service (FastAPI, PostgreSQL, Celery/Redis) that runs scheduled
  SQL-pushdown validation checks on external databases, with deduplicated alerting, Prometheus
  metrics, Docker, and CI covering 93% of the code.
