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

    # Simple shared-secret auth for the API (sent as the X-API-Key header).
    api_key: str = "dev-api-key"

    # Celery. When task_always_eager is true, tasks run inline in the API process,
    # which means no Redis is needed for local development or tests.
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    celery_task_always_eager: bool = True

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
