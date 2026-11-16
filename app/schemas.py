"""Pydantic request/response models: the API contract.

Request models validate input (and call into the check engine to validate rule
configs) before anything touches the database. Response models control exactly what
leaves the API, e.g. connection URLs are always returned with the password masked.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.engine.checks import CheckConfigError, validate_rule
from app.engine.connectors import ConnectionConfigError, mask_url, validate_connection
from app.services.scheduler import is_valid_cron

Severity = Literal["warning", "critical"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


# ---- Data sources ----------------------------------------------------------
def _check_cron(v: str | None) -> str | None:
    if v is not None and not is_valid_cron(v):
        raise ValueError(f"Invalid cron expression: {v!r}")
    return v


class DataSourceBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    source_type: str = Field(examples=["postgresql", "sqlite"])
    connection_config: dict[str, Any] = Field(
        examples=[{"url": "postgresql://readonly:secret@warehouse:5432/analytics"}]
    )
    schedule_cron: str | None = Field(default=None, examples=["*/15 * * * *"])
    is_active: bool = True

    @field_validator("schedule_cron")
    @classmethod
    def _cron(cls, v: str | None) -> str | None:
        return _check_cron(v)


class DataSourceCreate(DataSourceBase):
    @model_validator(mode="after")
    def _connection(self) -> "DataSourceCreate":
        try:
            validate_connection(self.source_type, self.connection_config)
        except ConnectionConfigError as exc:
            raise ValueError(str(exc)) from exc
        return self


class DataSourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    connection_config: dict[str, Any] | None = None
    schedule_cron: str | None = None
    is_active: bool | None = None

    @field_validator("schedule_cron")
    @classmethod
    def _cron(cls, v: str | None) -> str | None:
        return _check_cron(v)


class DataSourceOut(ORMModel):
    id: int
    name: str
    source_type: str
    connection_config: dict[str, Any]
    schedule_cron: str | None
    is_active: bool
    last_scheduled_at: datetime | None
    created_at: datetime

    @field_validator("connection_config")
    @classmethod
    def _mask(cls, v: dict[str, Any]) -> dict[str, Any]:
        return {**v, "url": mask_url(v["url"])} if "url" in v else v


# ---- Validation rules ------------------------------------------------------
class RuleBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    rule_type: str = Field(examples=["not_null"])
    table_name: str = Field(examples=["orders"])
    column_name: str | None = Field(default=None, examples=["customer_id"])
    rule_config: dict[str, Any] = Field(default_factory=dict)
    severity: Severity = "warning"
    threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    is_active: bool = True


class RuleCreate(RuleBase):
    data_source_id: int

    @model_validator(mode="after")
    def _validate(self) -> "RuleCreate":
        try:
            validate_rule(self.rule_type, self.table_name, self.column_name, self.rule_config)
        except CheckConfigError as exc:
            raise ValueError(str(exc)) from exc
        return self


class RuleUpdate(BaseModel):
    name: str | None = None
    table_name: str | None = None
    column_name: str | None = None
    rule_config: dict[str, Any] | None = None
    severity: Severity | None = None
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    is_active: bool | None = None


class RuleOut(ORMModel, RuleBase):
    id: int
    data_source_id: int
    created_at: datetime


class RuleTypeOut(BaseModel):
    rule_type: str
    requires_column: bool
    description: str


# ---- Runs and results ------------------------------------------------------
class CheckResultOut(ORMModel):
    id: int
    run_id: int
    rule_id: int
    status: str
    total_rows: int | None
    failing_rows: int | None
    observed_value: float | None
    message: str
    sample_failures: list[Any]
    duration_ms: float
    created_at: datetime


class RunOut(ORMModel):
    id: int
    data_source_id: int
    status: str
    trigger: str
    started_at: datetime | None
    finished_at: datetime | None
    total_rules: int
    passed_rules: int
    failed_rules: int
    error_rules: int
    error_message: str | None
    created_at: datetime


class RunDetailOut(RunOut):
    results: list[CheckResultOut]


# ---- Alerts ----------------------------------------------------------------
class AlertOut(ORMModel):
    id: int
    rule_id: int
    last_result_id: int | None
    severity: str
    status: str
    message: str
    occurrences: int
    last_seen_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    created_at: datetime


# ---- Dashboard -------------------------------------------------------------
class SourceHealth(BaseModel):
    data_source_id: int
    name: str
    last_run_id: int | None
    last_run_status: str | None
    last_run_at: datetime | None
    pass_rate: float | None
    open_alerts: int


class SummaryOut(BaseModel):
    data_sources: int
    active_rules: int
    open_alerts: int
    critical_open_alerts: int
    results_last_24h: int
    pass_rate_last_24h: float | None
    sources: list[SourceHealth]
