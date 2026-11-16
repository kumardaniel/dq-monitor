"""ORM models (SQLAlchemy 2.0 typed style).

Entity overview:
    DataSource 1--* ValidationRule      what to check, and where
    DataSource 1--* CheckRun            one execution of all active rules of a source
    CheckRun   1--* CheckResult         the outcome of one rule in one run
    ValidationRule 1--* Alert           opened when a rule fails, resolved when it passes again
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DataSource(TimestampMixin, Base):
    __tablename__ = "data_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    source_type: Mapped[str] = mapped_column(String(32))  # postgresql | sqlite | mysql
    # Holds {"url": "<sqlalchemy url>"}; never returned unmasked by the API.
    connection_config: Mapped[dict[str, Any]] = mapped_column(JSON)
    # Cron expression (e.g. "*/15 * * * *"). NULL means manual runs only.
    schedule_cron: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    validation_rules: Mapped[list["ValidationRule"]] = relationship(
        back_populates="data_source", cascade="all, delete-orphan"
    )
    runs: Mapped[list["CheckRun"]] = relationship(
        back_populates="data_source", cascade="all, delete-orphan"
    )


class ValidationRule(TimestampMixin, Base):
    __tablename__ = "validation_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    data_source_id: Mapped[int] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    rule_type: Mapped[str] = mapped_column(String(32))
    table_name: Mapped[str] = mapped_column(String(128))
    column_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Rule-type specific parameters, e.g. {"min": 0, "max": 100} for a range check.
    rule_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    severity: Mapped[str] = mapped_column(String(16), default="warning")  # warning | critical
    # Fraction of rows allowed to fail before the rule counts as failed (0.0 = strict).
    threshold: Mapped[float] = mapped_column(Float, default=0.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    data_source: Mapped[DataSource] = relationship(back_populates="validation_rules")
    results: Mapped[list["CheckResult"]] = relationship(
        back_populates="rule", cascade="all, delete-orphan"
    )
    alerts: Mapped[list["Alert"]] = relationship(
        back_populates="rule", cascade="all, delete-orphan"
    )


class CheckRun(TimestampMixin, Base):
    __tablename__ = "check_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    data_source_id: Mapped[int] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    trigger: Mapped[str] = mapped_column(String(16), default="manual")  # manual | scheduled
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_rules: Mapped[int] = mapped_column(Integer, default=0)
    passed_rules: Mapped[int] = mapped_column(Integer, default=0)
    failed_rules: Mapped[int] = mapped_column(Integer, default=0)
    error_rules: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    data_source: Mapped[DataSource] = relationship(back_populates="runs")
    results: Mapped[list["CheckResult"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="CheckResult.id"
    )


class CheckResult(TimestampMixin, Base):
    __tablename__ = "check_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("check_runs.id", ondelete="CASCADE"), index=True)
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("validation_rules.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(16))  # passed | failed | error
    total_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failing_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    observed_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str] = mapped_column(Text, default="")
    sample_failures: Mapped[list[Any]] = mapped_column(JSON, default=list)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)

    run: Mapped[CheckRun] = relationship(back_populates="results")
    rule: Mapped[ValidationRule] = relationship(back_populates="results")


class Alert(TimestampMixin, Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("validation_rules.id", ondelete="CASCADE"), index=True
    )
    last_result_id: Mapped[int | None] = mapped_column(
        ForeignKey("check_results.id", ondelete="SET NULL"), nullable=True
    )
    severity: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    message: Mapped[str] = mapped_column(Text)
    # How many consecutive failing runs this alert has absorbed (deduplication).
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    rule: Mapped[ValidationRule] = relationship(back_populates="alerts")
