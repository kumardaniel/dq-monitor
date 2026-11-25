# Resume entry

**DQ Monitor: Data Quality Monitoring Service** · Python, FastAPI, PostgreSQL, SQLAlchemy,
RabbitMQ, Celery, Redis, JWT, Docker, GitHub Actions · [github.com/&lt;you&gt;/dq-monitor](#)

- Built a REST backend (FastAPI, SQLAlchemy 2.0, PostgreSQL, Alembic) that runs 8 types of
  data quality checks against external databases: null, uniqueness, range, regex, freshness,
  row count and custom SQL. Checks are compiled to SQL and pushed down to the database, so
  cost stays flat as tables grow.
- Moved check execution to Celery workers on RabbitMQ, with cron scheduling. Used ack-late
  delivery, idempotent run processing, retries with exponential backoff, and a dead-letter
  queue with a replay tool, so failed jobs are never silently lost.
- Implemented JWT authentication with bcrypt password hashing and viewer/editor roles, plus
  algorithm pinning, timing-safe login, and Redis-backed login rate limiting.
- Added a Redis cache-aside layer for hot reads, with commit-then-invalidate consistency, TTL
  backstops, and fail-open behaviour when Redis is unavailable.
- Designed an alert lifecycle with deduplication and auto-resolve, which cut repeat
  notifications to one per incident.
- Shipped with structured JSON logging, Prometheus metrics, and a unified error format.
  81 pytest tests cover 94% of the code. GitHub Actions CI runs lint, tests, Postgres
  migration round-trips and a full Docker Compose end-to-end test.

Short version (2 lines):

- Built a data quality monitoring service (FastAPI, PostgreSQL, RabbitMQ/Celery, Redis, JWT)
  that runs scheduled SQL-pushdown checks with retries, a dead-letter queue, cache-aside
  reads, role-based auth, and CI covering 94% of the code.
