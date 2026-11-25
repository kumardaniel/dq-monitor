"""Application settings, loaded from environment variables (and an optional .env file).

pydantic-settings validates every value at startup, so a typo in an env var fails fast
instead of surfacing as a confusing runtime error later.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "DQ Monitor"
    environment: str = "local"
    log_level: str = "INFO"

    # Metadata store: where DQ Monitor keeps sources, rules, runs, results and alerts.
    # Defaults to SQLite so the project runs with zero setup; docker-compose uses Postgres.
    database_url: str = "sqlite:///./dq_monitor.db"

    # JWT auth. The secret signs tokens: anyone holding it can mint valid tokens, so
    # production must override it (the app refuses to start outside "local"/"test" if not).
    jwt_secret: str = "dev-insecure-secret-change-me-before-deploying"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 60
    # bcrypt work factor: each +1 doubles hashing time. Tests lower it to stay fast.
    bcrypt_rounds: int = 12

    # Optional first user, created at startup only if the users table is empty.
    bootstrap_editor_email: str | None = None
    bootstrap_editor_password: str | None = None

    # Celery over RabbitMQ. When task_always_eager is true, tasks run inline in the API
    # process, so no broker is needed for local development or tests.
    celery_broker_url: str = "amqp://guest:guest@localhost:5672//"
    celery_task_always_eager: bool = True
    # Retries for a run whose infrastructure failed (e.g. metadata DB briefly down)
    # before the message is dead-lettered.
    run_task_max_retries: int = 3

    # Redis: read cache + rate limiting. Unset = both disabled (the app still works).
    redis_url: str | None = None
    cache_ttl_seconds: int = 60
    rate_limit_per_minute: int = 120  # per client IP across /api/v1; 0 disables
    login_rate_limit_per_minute: int = 10  # per IP + email, against password guessing

    # How often the scheduler looks for data sources whose cron schedule is due.
    scheduler_tick_seconds: int = 60

    # Optional Slack-compatible incoming webhook for alert notifications.
    alert_webhook_url: str | None = None
    alert_webhook_timeout_seconds: float = 5.0

    # Safety limits applied when checks query a monitored data source.
    check_statement_timeout_ms: int = 30_000
    max_failing_samples: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
