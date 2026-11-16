"""Data quality check implementations.

Every check is "pushed down" to the monitored database as SQL: we never pull the
whole table into Python, we ask the database to count totals and failures. That keeps
checks fast on large tables and keeps memory use flat.

SQL is built with SQLAlchemy Core (table()/column()/select()) instead of string
formatting, so identifiers are quoted correctly for each dialect and user-supplied
values are always sent as bound parameters, never pasted into the SQL text.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import Connection, and_, column, func, literal_column, or_, select, table, text
from sqlalchemy.sql.elements import ColumnClause
from sqlalchemy.sql.selectable import TableClause

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


class CheckConfigError(ValueError):
    """Raised when a rule's configuration is invalid for its rule type."""


@dataclass
class CheckOutcome:
    status: str  # passed | failed | error
    message: str
    total_rows: int | None = None
    failing_rows: int | None = None
    observed_value: float | None = None
    sample_failures: list[Any] = field(default_factory=list)


@dataclass
class CheckContext:
    table_name: str
    column_name: str | None
    config: dict[str, Any]
    threshold: float = 0.0
    max_samples: int = 5


def validate_identifier(name: str, what: str) -> str:
    if not IDENTIFIER_RE.match(name):
        raise CheckConfigError(
            f"Invalid {what} {name!r}: use letters, digits and underscores only."
        )
    return name


def build_table(table_name: str) -> TableClause:
    """Accepts "table" or "schema.table"; each part is validated as an identifier."""
    parts = table_name.split(".")
    if len(parts) > 2:
        raise CheckConfigError("Table name must be 'table' or 'schema.table'.")
    for part in parts:
        validate_identifier(part, "table name")
    if len(parts) == 2:
        return table(parts[1], schema=parts[0])
    return table(parts[0])


def _scalar(conn: Connection, stmt: Any) -> Any:
    return conn.execute(stmt).scalar()


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class Check(ABC):
    """Base class. Subclasses declare their type name and implement validate/run."""

    rule_type: ClassVar[str]
    requires_column: ClassVar[bool] = True
    description: ClassVar[str] = ""

    @classmethod
    def validate_config(cls, column_name: str | None, config: dict[str, Any]) -> None:
        if cls.requires_column and not column_name:
            raise CheckConfigError(f"Rule type '{cls.rule_type}' requires column_name.")
        if column_name:
            validate_identifier(column_name, "column name")

    @abstractmethod
    def run(self, conn: Connection, ctx: CheckContext) -> CheckOutcome: ...


class RowLevelCheck(Check):
    """A check of the form "rows matching <failure condition> should not exist".

    Subclasses only provide the failure condition; counting, threshold comparison and
    collecting sample bad values is shared here.
    """

    @abstractmethod
    def failure_condition(self, col: ColumnClause, ctx: CheckContext) -> Any: ...

    def run(self, conn: Connection, ctx: CheckContext) -> CheckOutcome:
        tbl = build_table(ctx.table_name)
        col = column(ctx.column_name)  # type: ignore[arg-type]
        condition = self.failure_condition(col, ctx)

        total = _scalar(conn, select(func.count()).select_from(tbl)) or 0
        failing = _scalar(conn, select(func.count()).select_from(tbl).where(condition)) or 0
        samples: list[Any] = []
        if failing:
            rows = conn.execute(
                select(col).select_from(tbl).where(condition).limit(ctx.max_samples)
            )
            samples = [_jsonable(r[0]) for r in rows]
        return evaluate_ratio(total, failing, ctx, samples, self.rule_type)


def evaluate_ratio(
    total: int, failing: int, ctx: CheckContext, samples: list[Any], label: str
) -> CheckOutcome:
    if total == 0:
        return CheckOutcome("passed", "Table is empty; nothing to check.", 0, 0, 0.0)
    ratio = failing / total
    passed = ratio <= ctx.threshold
    message = (
        f"{label}: {failing}/{total} rows failing ({ratio:.2%}); threshold {ctx.threshold:.2%}."
    )
    return CheckOutcome("passed" if passed else "failed", message, total, failing, ratio, samples)


class NotNullCheck(RowLevelCheck):
    rule_type = "not_null"
    description = "Column must not contain NULLs."

    def failure_condition(self, col: ColumnClause, ctx: CheckContext) -> Any:
        return col.is_(None)


class RangeCheck(RowLevelCheck):
    rule_type = "range"
    description = "Numeric column must be within [min, max]. Config: {min?, max?}."

    @classmethod
    def validate_config(cls, column_name: str | None, config: dict[str, Any]) -> None:
        super().validate_config(column_name, config)
        if "min" not in config and "max" not in config:
            raise CheckConfigError("range requires at least one of 'min' or 'max'.")
        for key in ("min", "max"):
            if key in config and not isinstance(config[key], (int, float)):
                raise CheckConfigError(f"range '{key}' must be a number.")
        if "min" in config and "max" in config and config["min"] > config["max"]:
            raise CheckConfigError("range 'min' must be <= 'max'.")

    def failure_condition(self, col: ColumnClause, ctx: CheckContext) -> Any:
        conds = []
        if "min" in ctx.config:
            conds.append(col < ctx.config["min"])
        if "max" in ctx.config:
            conds.append(col > ctx.config["max"])
        return and_(col.is_not(None), or_(*conds))


class AcceptedValuesCheck(RowLevelCheck):
    rule_type = "accepted_values"
    description = "Column values must be in an allowed list. Config: {values: [...]}."

    @classmethod
    def validate_config(cls, column_name: str | None, config: dict[str, Any]) -> None:
        super().validate_config(column_name, config)
        values = config.get("values")
        if not isinstance(values, list) or not values:
            raise CheckConfigError("accepted_values requires a non-empty 'values' list.")

    def failure_condition(self, col: ColumnClause, ctx: CheckContext) -> Any:
        return and_(col.is_not(None), col.not_in(ctx.config["values"]))


class RegexCheck(RowLevelCheck):
    rule_type = "regex"
    description = "Text column must match a regular expression. Config: {pattern}."

    @classmethod
    def validate_config(cls, column_name: str | None, config: dict[str, Any]) -> None:
        super().validate_config(column_name, config)
        pattern = config.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise CheckConfigError("regex requires a 'pattern' string.")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise CheckConfigError(f"Invalid regex pattern: {exc}") from exc

    def failure_condition(self, col: ColumnClause, ctx: CheckContext) -> Any:
        # regexp_match compiles to `~` on PostgreSQL, REGEXP on MySQL/SQLite.
        return and_(col.is_not(None), ~col.regexp_match(ctx.config["pattern"]))


class UniqueCheck(Check):
    rule_type = "unique"
    description = "Column values must be unique (NULLs ignored)."

    def run(self, conn: Connection, ctx: CheckContext) -> CheckOutcome:
        tbl = build_table(ctx.table_name)
        col = column(ctx.column_name)  # type: ignore[arg-type]
        non_null = select(func.count(col)).select_from(tbl)
        distinct = select(func.count(func.distinct(col))).select_from(tbl)
        total = _scalar(conn, non_null) or 0
        failing = total - (_scalar(conn, distinct) or 0)  # rows that are duplicates
        samples: list[Any] = []
        if failing:
            dupes = (
                select(col, func.count().label("n"))
                .select_from(tbl)
                .where(col.is_not(None))
                .group_by(col)
                .having(func.count() > 1)
                .limit(ctx.max_samples)
            )
            samples = [{"value": _jsonable(r[0]), "count": r[1]} for r in conn.execute(dupes)]
        return evaluate_ratio(total, failing, ctx, samples, self.rule_type)


class RowCountCheck(Check):
    rule_type = "row_count"
    requires_column = False
    description = "Table row count must be within [min, max]. Config: {min?, max?}."

    @classmethod
    def validate_config(cls, column_name: str | None, config: dict[str, Any]) -> None:
        RangeCheck.validate_config("_", config)  # same min/max validation, no column needed

    def run(self, conn: Connection, ctx: CheckContext) -> CheckOutcome:
        tbl = build_table(ctx.table_name)
        count = _scalar(conn, select(func.count()).select_from(tbl)) or 0
        lo, hi = ctx.config.get("min"), ctx.config.get("max")
        ok = (lo is None or count >= lo) and (hi is None or count <= hi)
        bounds = f"[{lo if lo is not None else '-inf'}, {hi if hi is not None else 'inf'}]"
        return CheckOutcome(
            "passed" if ok else "failed",
            f"row_count: {count} rows; expected within {bounds}.",
            total_rows=count,
            observed_value=float(count),
        )


class FreshnessCheck(Check):
    rule_type = "freshness"
    description = "Newest timestamp in column must be recent. Config: {max_age_minutes}."

    @classmethod
    def validate_config(cls, column_name: str | None, config: dict[str, Any]) -> None:
        super().validate_config(column_name, config)
        age = config.get("max_age_minutes")
        if not isinstance(age, (int, float)) or age <= 0:
            raise CheckConfigError("freshness requires a positive 'max_age_minutes'.")

    def run(self, conn: Connection, ctx: CheckContext) -> CheckOutcome:
        tbl = build_table(ctx.table_name)
        col = column(ctx.column_name)  # type: ignore[arg-type]
        latest = _scalar(conn, select(func.max(col)).select_from(tbl))
        if latest is None:
            return CheckOutcome("failed", "freshness: table has no timestamps.")
        latest_dt = _to_datetime(latest)
        age_minutes = (datetime.now(UTC) - latest_dt).total_seconds() / 60
        limit = ctx.config["max_age_minutes"]
        ok = age_minutes <= limit
        return CheckOutcome(
            "passed" if ok else "failed",
            f"freshness: newest row is {age_minutes:.1f} min old; limit {limit} min.",
            observed_value=round(age_minutes, 2),
            sample_failures=[] if ok else [latest_dt.isoformat()],
        )


def _to_datetime(value: Any) -> datetime:
    if isinstance(value, str):  # SQLite stores timestamps as text
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        raise CheckConfigError(f"Column does not contain timestamps (got {type(value).__name__}).")
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class CustomSqlCheck(Check):
    """User-written SQL that SELECTs the *bad* rows. Zero rows returned = pass.

    Guard rails: single statement, must start with SELECT/WITH, and runs inside a
    read-only transaction on PostgreSQL. The real protection, documented in the README,
    is to give DQ Monitor a read-only database user for each monitored source.
    """

    rule_type = "custom_sql"
    requires_column = False
    description = "SQL query returning failing rows; passes when it returns none. Config: {sql}."

    _FORBIDDEN = re.compile(
        r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|merge|call)\b",
        re.IGNORECASE,
    )

    @classmethod
    def validate_config(cls, column_name: str | None, config: dict[str, Any]) -> None:
        sql = config.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            raise CheckConfigError("custom_sql requires a 'sql' string.")
        stripped = sql.strip().rstrip(";").strip()
        if ";" in stripped:
            raise CheckConfigError("custom_sql must be a single statement.")
        if not re.match(r"^(select|with)\b", stripped, re.IGNORECASE):
            raise CheckConfigError("custom_sql must start with SELECT or WITH.")
        if cls._FORBIDDEN.search(stripped):
            raise CheckConfigError("custom_sql may only read data.")

    def run(self, conn: Connection, ctx: CheckContext) -> CheckOutcome:
        sql = ctx.config["sql"].strip().rstrip(";")
        if conn.dialect.name == "postgresql":
            conn.execute(text("SET TRANSACTION READ ONLY"))
        sub = text(sql).columns().subquery("dq_custom")
        failing = _scalar(conn, select(func.count()).select_from(sub)) or 0
        samples: list[Any] = []
        if failing:
            rows = conn.execute(select(literal_column("*")).select_from(sub).limit(ctx.max_samples))
            samples = [{k: _jsonable(v) for k, v in r._mapping.items()} for r in rows]
        return CheckOutcome(
            "passed" if failing == 0 else "failed",
            f"custom_sql: query returned {failing} failing rows.",
            failing_rows=failing,
            observed_value=float(failing),
            sample_failures=samples,
        )


# Registry: rule_type -> check class. Adding a new check type = one class + one entry.
CHECKS: dict[str, type[Check]] = {
    cls.rule_type: cls
    for cls in (
        NotNullCheck,
        UniqueCheck,
        RangeCheck,
        AcceptedValuesCheck,
        RegexCheck,
        RowCountCheck,
        FreshnessCheck,
        CustomSqlCheck,
    )
}


def get_check(rule_type: str) -> Check:
    try:
        return CHECKS[rule_type]()
    except KeyError as exc:
        raise CheckConfigError(f"Unknown rule_type {rule_type!r}.") from exc


def validate_rule(
    rule_type: str, table_name: str, column_name: str | None, config: dict[str, Any]
) -> None:
    """Validate a rule definition before it is saved. Raises CheckConfigError."""
    if rule_type not in CHECKS:
        raise CheckConfigError(
            f"Unknown rule_type {rule_type!r}. Choose one of: {', '.join(sorted(CHECKS))}."
        )
    build_table(table_name)
    CHECKS[rule_type].validate_config(column_name, config)
