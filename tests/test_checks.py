"""Unit tests for the check engine, run directly against the seeded SQLite warehouse."""

import pytest
from sqlalchemy import create_engine

from app.engine.checks import CheckConfigError, CheckContext, get_check, validate_rule


@pytest.fixture
def conn(warehouse_url):
    engine = create_engine(warehouse_url)
    with engine.connect() as c:
        yield c
    engine.dispose()


def run(conn, rule_type, column=None, config=None, threshold=0.0, table="orders"):
    ctx = CheckContext(table, column, config or {}, threshold=threshold)
    return get_check(rule_type).run(conn, ctx)


def test_not_null_counts_nulls(conn):
    out = run(conn, "not_null", "customer_id")
    assert out.status == "failed"
    assert (out.total_rows, out.failing_rows) == (10, 2)
    assert out.observed_value == pytest.approx(0.2)


def test_threshold_allows_some_failures(conn):
    assert run(conn, "not_null", "customer_id", threshold=0.2).status == "passed"
    assert run(conn, "not_null", "customer_id", threshold=0.19).status == "failed"


def test_not_null_passes_on_clean_column(conn):
    assert run(conn, "not_null", "order_id").status == "passed"


def test_unique_finds_duplicates_with_samples(conn):
    out = run(conn, "unique", "order_id")
    assert out.status == "failed"
    assert out.failing_rows == 1
    assert out.sample_failures == [{"value": 7, "count": 2}]


def test_range(conn):
    out = run(conn, "range", "amount", {"min": 0, "max": 1000})
    assert out.failing_rows == 2
    assert sorted(out.sample_failures) == [-5.0, 2500.0]
    assert run(conn, "range", "amount", {"min": -10, "max": 3000}).status == "passed"


def test_accepted_values(conn):
    out = run(conn, "accepted_values", "status", {"values": ["paid", "shipped", "refunded"]})
    assert out.failing_rows == 1
    assert out.sample_failures == ["lost"]


def test_regex(conn):
    out = run(conn, "regex", "email", {"pattern": r"^[^@\s]+@[^@\s]+\.[a-z]+$"})
    assert out.failing_rows == 1
    assert out.sample_failures == ["not-an-email"]


def test_row_count(conn):
    assert run(conn, "row_count", config={"min": 5}).status == "passed"
    out = run(conn, "row_count", config={"min": 50})
    assert out.status == "failed"
    assert out.observed_value == 10


def test_freshness(conn):
    assert run(conn, "freshness", "updated_at", {"max_age_minutes": 60}).status == "passed"
    out = run(conn, "freshness", "updated_at", {"max_age_minutes": 5})
    assert out.status == "failed"
    assert 9 < out.observed_value < 12


def test_custom_sql(conn):
    out = run(conn, "custom_sql", config={"sql": "SELECT * FROM orders WHERE amount > 1000"})
    assert out.status == "failed"
    assert out.failing_rows == 1
    assert out.sample_failures[0]["order_id"] == 5
    ok = run(conn, "custom_sql", config={"sql": "SELECT * FROM orders WHERE amount > 99999"})
    assert ok.status == "passed"


def test_empty_table_passes(conn):
    out = run(conn, "not_null", "id", table="empty_table")
    assert out.status == "passed"
    assert out.total_rows == 0


@pytest.mark.parametrize(
    "rule_type,table,column,config,error",
    [
        ("nope", "orders", "a", {}, "Unknown rule_type"),
        ("not_null", "orders", None, {}, "requires column_name"),
        ("not_null", "orders; DROP TABLE x", "a", {}, "Invalid table name"),
        ("not_null", "orders", "a b", {}, "Invalid column name"),
        ("range", "orders", "a", {}, "at least one"),
        ("range", "orders", "a", {"min": 5, "max": 1}, "min"),
        ("accepted_values", "orders", "a", {"values": []}, "non-empty"),
        ("regex", "orders", "a", {"pattern": "("}, "Invalid regex"),
        ("freshness", "orders", "a", {"max_age_minutes": 0}, "positive"),
        ("custom_sql", "orders", None, {"sql": "DELETE FROM orders"}, "SELECT or WITH"),
        ("custom_sql", "orders", None, {"sql": "SELECT 1; DROP TABLE orders"}, "single"),
        ("custom_sql", "orders", None, {"sql": "WITH x AS (DELETE FROM t) SELECT 1"}, "only read"),
    ],
)
def test_rule_validation_rejects_bad_config(rule_type, table, column, config, error):
    with pytest.raises(CheckConfigError, match=error):
        validate_rule(rule_type, table, column, config)


def test_schema_qualified_table_is_allowed():
    validate_rule("not_null", "analytics.orders", "id", {})
