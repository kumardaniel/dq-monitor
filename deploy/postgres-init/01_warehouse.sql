-- Demo "warehouse" database that DQ Monitor will monitor. Contains deliberate defects.
CREATE DATABASE warehouse;
\connect warehouse

-- A read-only user: the recommended way to connect DQ Monitor to any real source.
CREATE USER dq_reader WITH PASSWORD 'dq_reader';

CREATE TABLE orders (
    order_id     INTEGER,
    customer_id  INTEGER,
    amount       NUMERIC(10, 2),
    status       TEXT,
    email        TEXT,
    updated_at   TIMESTAMPTZ
);

INSERT INTO orders
SELECT
    g,                                                         -- order_id
    CASE WHEN g % 50 = 0 THEN NULL ELSE 1000 + g END,          -- 2% NULL customer_id
    CASE WHEN g % 97 = 0 THEN -10 ELSE (g % 500) + 0.99 END,   -- a few negative amounts
    (ARRAY['paid','shipped','refunded','paid','lost'])[1 + g % 5],
    CASE WHEN g % 120 = 0 THEN 'broken-email' ELSE 'user' || g || '@example.com' END,
    now() - (g || ' seconds')::interval
FROM generate_series(1, 5000) AS g;

INSERT INTO orders SELECT * FROM orders WHERE order_id <= 3;  -- duplicate ids

GRANT CONNECT ON DATABASE warehouse TO dq_reader;
GRANT USAGE ON SCHEMA public TO dq_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO dq_reader;
