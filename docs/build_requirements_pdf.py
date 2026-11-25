"""Builds docs/DQ_Monitor_Requirements.pdf.

pip install reportlab
python docs/build_requirements_pdf.py
"""

import math
from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

OUT = Path(__file__).with_name("DQ_Monitor_Requirements.pdf")
VERSION = "2.0"
INK = colors.HexColor("#1f2937")
ACCENT = colors.HexColor("#2563eb")
MUTED = colors.HexColor("#6b7280")
LINE = colors.HexColor("#d1d5db")
FILL = colors.HexColor("#eff6ff")
HEAD = colors.HexColor("#1e3a8a")
DB_FILL = colors.HexColor("#ecfdf5")
DB_STROKE = colors.HexColor("#059669")
EXT_FILL = colors.HexColor("#f3f4f6")
Q_FILL = colors.HexColor("#fff7ed")
Q_STROKE = colors.HexColor("#ea580c")

ss = getSampleStyleSheet()
body = ParagraphStyle(
    "body",
    parent=ss["BodyText"],
    fontName="Helvetica",
    fontSize=9.5,
    leading=13.5,
    textColor=INK,
    spaceAfter=5,
)
small = ParagraphStyle("small", parent=body, fontSize=8.5, leading=11.5, spaceAfter=0)
cell = ParagraphStyle("cell", parent=small)
cellb = ParagraphStyle("cellb", parent=small, fontName="Helvetica-Bold", textColor=colors.white)
h1 = ParagraphStyle(
    "h1",
    parent=ss["Heading1"],
    fontName="Helvetica-Bold",
    fontSize=15,
    textColor=HEAD,
    spaceBefore=10,
    spaceAfter=8,
)
h2 = ParagraphStyle(
    "h2",
    parent=ss["Heading2"],
    fontName="Helvetica-Bold",
    fontSize=11.5,
    textColor=INK,
    spaceBefore=8,
    spaceAfter=4,
)
title = ParagraphStyle("title", parent=h1, fontSize=28, leading=34, alignment=TA_CENTER)
subtitle = ParagraphStyle(
    "subtitle", parent=body, fontSize=13, leading=18, alignment=TA_CENTER, textColor=MUTED
)
code = ParagraphStyle("code", parent=small, fontName="Courier", fontSize=8, leading=10.5)


def P(t, s=body):
    return Paragraph(t, s)


def mono(t):
    return f"<font face='Courier'>{t}</font>"


def table(rows, widths, header=True):
    data = [
        [P(c, cellb if (header and i == 0) else cell) if isinstance(c, str) else c for c in r]
        for i, r in enumerate(rows)
    ]
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ]
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), HEAD),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f9fafb")]),
        ]
    t.setStyle(TableStyle(style))
    return t


def bullets(items):
    return [P(f"&bull;&nbsp; {i}") for i in items]


def label(d, x, y, text, anchor="middle"):
    d.add(
        String(x, y, text, fontName="Helvetica", fontSize=6.5, fillColor=MUTED, textAnchor=anchor)
    )


def line(d, x1, y1, x2, y2, dashed=False):
    d.add(
        Line(
            x1,
            y1,
            x2,
            y2,
            strokeColor=MUTED,
            strokeWidth=1,
            strokeDashArray=[3, 2] if dashed else None,
        )
    )


def arrow(d, x1, y1, x2, y2, dashed=False):
    line(d, x1, y1, x2, y2, dashed)
    a = math.atan2(y2 - y1, x2 - x1)
    s = 5
    d.add(
        Polygon(
            [
                x2,
                y2,
                x2 - s * math.cos(a - 0.4),
                y2 - s * math.sin(a - 0.4),
                x2 - s * math.cos(a + 0.4),
                y2 - s * math.sin(a + 0.4),
            ],
            fillColor=MUTED,
            strokeColor=MUTED,
        )
    )


def box(d, x, y, w, h, text, sub=None, fill=FILL, stroke=ACCENT):
    d.add(Rect(x, y, w, h, rx=5, ry=5, fillColor=fill, strokeColor=stroke, strokeWidth=1))
    ty = y + h / 2 + (2 if sub else -3)
    d.add(
        String(
            x + w / 2,
            ty,
            text,
            fontName="Helvetica-Bold",
            fontSize=8,
            fillColor=INK,
            textAnchor="middle",
        )
    )
    if sub:
        d.add(
            String(
                x + w / 2,
                ty - 10,
                sub,
                fontName="Helvetica",
                fontSize=6.5,
                fillColor=MUTED,
                textAnchor="middle",
            )
        )


def architecture():
    d = Drawing(480, 250)
    box(d, 0, 185, 75, 40, "API client", "Bearer JWT", EXT_FILL, MUTED)
    box(d, 130, 185, 100, 40, "FastAPI API", "auth, CRUD, reads")
    box(d, 275, 185, 90, 40, "RabbitMQ", "queue dq.tasks", Q_FILL, Q_STROKE)
    box(d, 400, 200, 80, 30, "dq.dead", "dead-letter queue", Q_FILL, Q_STROKE)
    box(d, 275, 95, 90, 40, "Celery workers", "run checks")
    box(d, 130, 95, 100, 40, "PostgreSQL", "system of record", DB_FILL, DB_STROKE)
    box(d, 0, 95, 75, 40, "Redis", "cache + rate limit", DB_FILL, DB_STROKE)
    box(d, 400, 140, 80, 36, "Monitored DB(s)", "read-only user", DB_FILL, DB_STROKE)
    box(d, 400, 70, 80, 36, "Slack / webhook", None, EXT_FILL, MUTED)
    box(d, 275, 10, 90, 36, "Celery beat", "cron scheduler")

    arrow(d, 75, 205, 130, 205)
    label(d, 102, 209, "HTTPS")
    arrow(d, 230, 205, 275, 205)
    label(d, 252, 209, "publish")
    arrow(d, 365, 210, 400, 215, dashed=True)
    label(d, 382, 222, "rejected")
    arrow(d, 320, 185, 320, 135)
    label(d, 324, 160, "consume", "start")
    arrow(d, 180, 185, 180, 135)
    label(d, 184, 160, "read / write", "start")
    arrow(d, 275, 115, 230, 115)
    label(d, 252, 119, "results")
    arrow(d, 140, 185, 60, 135)
    label(d, 88, 168, "cache", "end")
    arrow(d, 365, 122, 400, 152)
    label(d, 388, 128, "SQL checks", "start")
    arrow(d, 365, 108, 400, 92)
    label(d, 386, 107, "POST", "start")
    arrow(d, 320, 46, 320, 95)
    label(d, 324, 68, "due runs", "start")
    line(d, 290, 95, 290, 70)
    line(d, 290, 70, 37, 70)
    arrow(d, 37, 70, 37, 95)
    label(d, 160, 61, "invalidate cache keys after commit")
    return d


def on_page(canvas, doc):
    if doc.page == 1:
        return
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(
        18 * mm, 12 * mm, f"DQ Monitor: Software Requirements Specification v{VERSION}"
    )
    canvas.drawRightString(A4[0] - 18 * mm, 12 * mm, f"Page {doc.page}")
    canvas.setStrokeColor(LINE)
    canvas.line(18 * mm, 15 * mm, A4[0] - 18 * mm, 15 * mm)
    canvas.restoreState()


W = A4[0] - 36 * mm
s = []

# ---------------- Cover ----------------
s += [
    Spacer(1, 60 * mm),
    P("DQ Monitor", title),
    Spacer(1, 4),
    P("Data Quality Monitoring Service", subtitle),
    Spacer(1, 2),
    P("Software Requirements Specification", subtitle),
    Spacer(1, 20 * mm),
]
s.append(
    table(
        [
            ["Document", "Value"],
            ["Version", VERSION],
            ["Status", "Implemented"],
            ["Date", "25 November 2026"],
            ["Repository", "dq-monitor (GitHub)"],
            [
                "Stack",
                "Python 3.12, FastAPI, SQLAlchemy 2.0, PostgreSQL, Alembic, Celery, RabbitMQ, "
                "Redis, JWT (PyJWT, bcrypt), Prometheus, Docker, GitHub Actions",
            ],
            [
                "Changes in 2.0",
                "JWT authentication with viewer/editor roles; RabbitMQ broker with "
                "retries and a dead-letter queue; Redis read cache and rate limiting; "
                "unified error format.",
            ],
        ],
        [40 * mm, W - 40 * mm],
    )
)
s.append(PageBreak())

# ---------------- 1. Introduction ----------------
s.append(P("1. Introduction", h1))
s.append(P("1.1 Purpose", h2))
s.append(
    P(
        "This document specifies the requirements for <b>DQ Monitor</b>, a backend service "
        "that continuously validates data stored in relational databases and alerts "
        "operators when the data violates expectations. It is the reference for design, "
        "implementation, testing and acceptance."
    )
)
s.append(P("1.2 Problem statement", h2))
s.append(
    P(
        "Data pipelines fail silently. A broken upstream job can load NULL customer IDs, "
        "duplicate orders, negative amounts or stale data, and nobody notices until a "
        "dashboard or a customer report is wrong. Teams need an automated, scheduled way to "
        "assert facts about their data and be told quickly, once, when those facts stop "
        "being true."
    )
)
s.append(P("1.3 Scope", h2))
s.append(
    P(
        "In scope: user authentication and roles; registering data sources; defining "
        "validation rules; executing rules on demand and on a cron schedule through a "
        "message queue; storing results and history; managing alerts and webhook "
        "notifications; cached status reads; operational endpoints; containerised "
        "deployment and CI."
    )
)
s.append(
    P(
        "Out of scope for v2: a web UI, multi-tenant (per-team) data isolation, non-SQL "
        "sources (files, APIs, streams), ML-based anomaly detection."
    )
)
s.append(P("1.4 Glossary", h2))
s.append(
    table(
        [
            ["Term", "Definition"],
            [
                "Data source",
                "A database DQ Monitor connects to and checks (the <i>monitored</i> DB).",
            ],
            [
                "Metadata DB",
                "DQ Monitor's own PostgreSQL database: users, sources, rules, runs, results, alerts.",
            ],
            [
                "Validation rule",
                'An assertion about a table or column, e.g. "customer_id is never NULL".',
            ],
            ["Run", "One execution of all active rules of one data source."],
            ["Result", "The outcome of one rule within one run: passed, failed or error."],
            [
                "Threshold",
                "The fraction of rows (0.0 to 1.0) allowed to fail before a rule counts as failed.",
            ],
            ["Alert", "An incident opened when a rule fails, deduplicated until it is resolved."],
            ["Pushdown", "Executing the check as SQL inside the monitored database."],
            ["JWT", "JSON Web Token: a signed, expiring access token identifying a user."],
            [
                "Dead-letter queue",
                "A queue holding messages that failed permanently, for inspection and replay.",
            ],
            [
                "Cache-aside",
                "Reads populate the cache on a miss; writes delete affected cache entries.",
            ],
        ],
        [32 * mm, W - 32 * mm],
    )
)

# ---------------- 2. Overall description ----------------
s.append(P("2. Overall Description", h1))
s.append(P("2.1 Users and roles", h2))
s.append(
    table(
        [
            ["Actor", "Role", "Goals"],
            [
                "Data engineer",
                "editor",
                "Register sources, define rules, schedule and trigger runs.",
            ],
            ["On-call engineer", "editor", "Acknowledge and resolve alerts."],
            ["Analyst / data consumer", "viewer", "Check dataset health before trusting a report."],
            [
                "Platform / SRE",
                "n/a",
                "Deploy, scale and monitor the service; manage the dead-letter queue.",
            ],
            [
                "Automated client",
                "editor or viewer",
                "CI jobs or orchestrators triggering runs or reading status.",
            ],
        ],
        [38 * mm, 26 * mm, W - 64 * mm],
    )
)
s.append(P("2.2 Operating environment", h2))
s += bullets(
    [
        "Linux containers (Docker image, Python 3.12). Local development also on macOS without Docker.",
        "PostgreSQL 14+ for the metadata DB (SQLite permitted for local development and tests).",
        "RabbitMQ 3.12+ as the message broker; Redis 7+ for caching and rate limiting.",
        "Monitored sources: PostgreSQL and SQLite; designed to add any SQLAlchemy dialect.",
    ]
)
s.append(P("2.3 Assumptions and constraints", h2))
s += bullets(
    [
        "Each monitored source is reachable from the workers over the network.",
        "Operators provide a read-only database user for each monitored source.",
        "Monitored tables can be large (100M+ rows); checks must not move full tables over the network.",
        "Redis holds only derived data; losing it must not lose information.",
    ]
)

# ---------------- 3. Functional requirements ----------------
s.append(P("3. Functional Requirements", h1))
s.append(
    P(
        "Priority uses MoSCoW: <b>M</b> = Must, <b>S</b> = Should, <b>C</b> = Could. "
        "All Must and Should requirements below are implemented in v2.0."
    )
)


def fr_table(rows):
    return table([["ID", "Requirement", "Pri."]] + rows, [17 * mm, W - 29 * mm, 12 * mm])


s.append(P("3.1 Authentication and authorization", h2))
s.append(
    fr_table(
        [
            [
                "FR-1",
                "Users shall log in with email and password and receive a signed, expiring JWT "
                "access token (OAuth2 password flow).",
                "M",
            ],
            [
                "FR-2",
                "Passwords shall be stored only as salted bcrypt hashes and be 8 to 72 bytes long.",
                "M",
            ],
            [
                "FR-3",
                "All /api/v1 endpoints except login shall require a valid token (HTTP 401 "
                "otherwise). Expired, forged and unsigned tokens shall be rejected.",
                "M",
            ],
            [
                "FR-4",
                "Users shall have a role: <i>viewer</i> may read; <i>editor</i> may also create, "
                "update, delete, trigger runs and change alerts. Violations return HTTP 403.",
                "M",
            ],
            [
                "FR-5",
                "Editors shall be able to create users, list users, change roles, reset passwords "
                "and deactivate users. Editors cannot demote or deactivate themselves.",
                "M",
            ],
            [
                "FR-6",
                "A deactivated user or changed role shall take effect on the next request, even "
                "for tokens that have not expired.",
                "M",
            ],
            ["FR-7", "Login failures shall not reveal whether an email is registered.", "S"],
            [
                "FR-8",
                "The first editor shall be creatable from configuration (only into an empty user "
                "table) or from a command-line tool.",
                "S",
            ],
        ]
    )
)
s.append(P("3.2 Data source management", h2))
s.append(
    fr_table(
        [
            [
                "FR-9",
                "Editors shall register a data source with a unique name, a source type "
                "(postgresql, sqlite) and a connection URL; malformed or mismatched URLs return "
                "HTTP 422, duplicate names HTTP 409.",
                "M",
            ],
            [
                "FR-10",
                "Connection passwords shall never be returned by the API; they are masked.",
                "M",
            ],
            [
                "FR-11",
                "Data sources shall support list (paginated), get, partial update and delete. "
                "Deleting a source deletes its rules, runs, results and alerts.",
                "M",
            ],
            [
                "FR-12",
                "A test-connection endpoint shall report whether the source is reachable.",
                "S",
            ],
            [
                "FR-13",
                "A data source may have an optional, validated cron schedule and an active flag.",
                "M",
            ],
        ]
    )
)
s.append(P("3.3 Validation rules", h2))
s.append(
    fr_table(
        [
            [
                "FR-14",
                "Supported rule types: not_null, unique, range, accepted_values, regex, "
                "row_count, freshness and custom_sql.",
                "M",
            ],
            [
                "FR-15",
                "Each rule specifies a table, a column where required, type-specific "
                "configuration, severity (warning | critical), threshold in [0, 1] and active flag.",
                "M",
            ],
            [
                "FR-16",
                "Rule configuration shall be validated against its type on create and update "
                "(HTTP 422 with a field-level reason).",
                "M",
            ],
            [
                "FR-17",
                "Table and column names shall be restricted to identifier characters; custom_sql "
                "shall accept a single read-only SELECT/WITH statement only.",
                "M",
            ],
            [
                "FR-18",
                "Supported rule types shall be listable; rules filterable by source and paginated.",
                "S",
            ],
        ]
    )
)
s.append(P("3.4 Check execution and queueing", h2))
s.append(
    fr_table(
        [
            [
                "FR-19",
                "Triggering a run shall respond <b>202 Accepted</b> immediately; the run is "
                "published to a RabbitMQ queue and executed by a worker.",
                "M",
            ],
            [
                "FR-20",
                "A run shall not start while another run of the same source is queued or "
                "running (HTTP 409).",
                "M",
            ],
            [
                "FR-21",
                "A run shall execute every active rule and record per rule: status, total rows, "
                "failing rows, observed value, message, sample failing values and duration.",
                "M",
            ],
            [
                "FR-22",
                "Row-level rules pass when failing_rows / total_rows &lt;= threshold. A rule that "
                "cannot execute is recorded as <i>error</i>; other rules still run.",
                "M",
            ],
            [
                "FR-23",
                "If run execution fails for infrastructure reasons, the task shall be retried "
                "with exponential backoff; after the last retry the run is marked <i>failed</i> "
                "and the message moved to a <b>dead-letter queue</b>.",
                "M",
            ],
            [
                "FR-24",
                "Notifications that fail after their retries shall also be dead-lettered.",
                "M",
            ],
            [
                "FR-25",
                "Operators shall be able to list dead-lettered tasks without removing them, and "
                "replay them with a fresh retry budget.",
                "S",
            ],
            [
                "FR-26",
                "If the broker is unreachable when a run is triggered, the API shall return "
                "HTTP 503 and mark the run failed rather than leave it queued.",
                "M",
            ],
            [
                "FR-27",
                "Run details shall be retrievable with all results; runs listable globally "
                "(filter by status) and per source; per-rule result history retrievable.",
                "M",
            ],
        ]
    )
)
s.append(P("3.5 Scheduling", h2))
s.append(
    fr_table(
        [
            [
                "FR-28",
                "A scheduler shall periodically enqueue runs for every active source whose cron "
                "schedule is due (trigger = scheduled), skipping sources with a run in flight.",
                "M",
            ],
            [
                "FR-29",
                "Concurrent scheduler instances shall not dispatch the same source twice.",
                "S",
            ],
        ]
    )
)
s.append(P("3.6 Alerting", h2))
s.append(
    fr_table(
        [
            [
                "FR-30",
                "A failed or errored rule with no active alert opens an alert with the rule's "
                "severity; repeat failures increment the active alert's occurrence count.",
                "M",
            ],
            [
                "FR-31",
                "An active alert is resolved automatically when its rule passes; editors can "
                "acknowledge and resolve alerts (invalid transitions return HTTP 409).",
                "M",
            ],
            [
                "FR-32",
                "If configured, a Slack-compatible webhook is called when an alert opens or "
                "resolves (not on repeats), with retries that never fail the run.",
                "S",
            ],
            ["FR-33", "Alerts shall be filterable by status and severity.", "M"],
        ]
    )
)
s.append(P("3.7 Status, dashboard and caching", h2))
s.append(
    fr_table(
        [
            [
                "FR-34",
                "A per-source status endpoint shall return health (healthy | failing | unknown), "
                "the latest run and open/critical alert counts.",
                "M",
            ],
            [
                "FR-35",
                "A dashboard summary shall return totals, open/critical alerts, 24-hour pass rate "
                "and per-source latest run, pass rate and open alerts.",
                "S",
            ],
            [
                "FR-36",
                "Status and summary responses shall be cached in Redis and invalidated whenever "
                "a run, alert, rule or source changes; responses indicate HIT or MISS.",
                "M",
            ],
        ]
    )
)
s.append(P("3.8 Errors", h2))
s.append(
    fr_table(
        [
            [
                "FR-37",
                "Every error response shall use one JSON format with a stable error code, a "
                "message, optional field-level details and the request ID.",
                "M",
            ],
            [
                "FR-38",
                "Unexpected server errors shall return HTTP 500 without exposing internals.",
                "M",
            ],
        ]
    )
)

# ---------------- 4. Non-functional ----------------
s.append(PageBreak())
s.append(P("4. Non-Functional Requirements", h1))
s.append(
    table(
        [
            ["ID", "Category", "Requirement"],
            [
                "NFR-1",
                "Performance",
                "Checks shall be pushed down as SQL aggregates; a check's memory "
                "use shall not grow with table size.",
            ],
            [
                "NFR-2",
                "Performance",
                "Non-run endpoints shall respond in &lt; 200 ms p95 at nominal "
                "load; cached status reads shall not query the metadata DB on a hit.",
            ],
            [
                "NFR-3",
                "Scalability",
                "API and workers shall be stateless (JWT, shared Redis and broker) "
                "and horizontally scalable.",
            ],
            [
                "NFR-4",
                "Reliability",
                "Worker crashes shall cause redelivery (ack-late); run execution "
                "shall be idempotent; no task shall be silently dropped.",
            ],
            [
                "NFR-5",
                "Reliability",
                "Each check query on PostgreSQL shall be bounded by a statement "
                "timeout (default 30 s).",
            ],
            [
                "NFR-6",
                "Availability",
                "If Redis is unavailable the API shall keep working (cache and "
                "rate limiting fail open).",
            ],
            [
                "NFR-7",
                "Consistency",
                "Cache entries shall be invalidated only after the database "
                "commit, and expire within 60 s even if an invalidation is missed.",
            ],
            [
                "NFR-8",
                "Security",
                "Tokens: HS256 with an algorithm whitelist, 60-minute expiry, and "
                "a signing secret of at least 32 characters (startup fails otherwise outside "
                "local development).",
            ],
            [
                "NFR-9",
                "Security",
                "API requests shall be rate-limited per client IP; login attempts "
                "per IP and email (HTTP 429 with Retry-After).",
            ],
            [
                "NFR-10",
                "Security",
                "User values reach SQL only as bound parameters; identifiers are "
                "validated and quoted; check connections never commit; containers run as non-root.",
            ],
            [
                "NFR-11",
                "Observability",
                "Structured JSON logs (API and workers) with request IDs; "
                "Prometheus metrics for requests, checks, runs, alerts, cache hits and rate limiting.",
            ],
            [
                "NFR-12",
                "Operability",
                "Liveness (/health) and readiness (/ready) endpoints; a broker "
                "management UI exposing queue depths, including the dead-letter queue.",
            ],
            [
                "NFR-13",
                "Maintainability",
                "Schema changes only through versioned Alembic migrations.",
            ],
            [
                "NFR-14",
                "Quality",
                "Automated tests shall cover &gt;= 85% of application code; CI runs "
                "lint, tests, migrations against PostgreSQL and a Docker end-to-end test.",
            ],
            [
                "NFR-15",
                "Portability",
                "The full stack starts with one docker compose command; local "
                "development works without Docker, RabbitMQ or Redis.",
            ],
        ],
        [17 * mm, 26 * mm, W - 43 * mm],
    )
)

# ---------------- 5. Architecture ----------------
s.append(PageBreak())
s.append(P("5. System Architecture", h1))
s.append(
    P(
        "Three process types are built from a single Docker image: the <b>API</b> "
        "(FastAPI), <b>workers</b> (Celery) and the <b>scheduler</b> (Celery beat). "
        "PostgreSQL is the system of record, RabbitMQ carries jobs, and Redis holds only "
        "derived data (cache entries and rate-limit counters)."
    )
)
s.append(
    KeepTogether([architecture(), P("Figure 1. Component diagram. Dashed: dead-lettering.", small)])
)
s.append(Spacer(1, 6))
s.append(P("5.1 Run lifecycle", h2))
s += bullets(
    [
        f"An editor calls {mono('POST /api/v1/data-sources/{id}/runs')} with a Bearer token.",
        "API verifies the token, loads the user, enforces the editor role and rate limit, checks "
        "no run is in flight, inserts a <i>queued</i> run, <b>commits</b>, invalidates the cache "
        "and publishes the task to RabbitMQ; it returns 202.",
        "A worker consumes the message, skips it unless the run is still queued (idempotency), "
        "marks it <i>running</i>, executes each rule, records results and updates alerts.",
        "The run is marked <i>completed</i>, cached status is invalidated, and notifications are "
        "published as separate, retryable tasks.",
        "On infrastructure failure the run is re-queued and the task retried with backoff; after "
        "the final attempt the run is marked <i>failed</i> and the message is dead-lettered.",
    ]
)
s.append(P("5.2 Technology choices", h2))
s.append(
    table(
        [
            ["Concern", "Choice", "Rationale"],
            [
                "HTTP API",
                "FastAPI + uvicorn",
                "Type-driven validation, automatic OpenAPI docs, dependency injection.",
            ],
            [
                "Validation",
                "Pydantic v2",
                "Declarative schemas, field-level 422 errors, typed settings.",
            ],
            [
                "Persistence",
                "SQLAlchemy 2.0",
                "ORM for metadata; Core for portable, injection-safe check SQL.",
            ],
            ["Migrations", "Alembic", "Versioned, reviewable schema changes."],
            ["Metadata DB", "PostgreSQL", "Transactions, SKIP LOCKED, JSON, reliability."],
            [
                "Auth",
                "PyJWT + bcrypt",
                "Stateless tokens for horizontal scaling; slow salted hashes.",
            ],
            [
                "Messaging",
                "Celery + RabbitMQ",
                "Durable queues, acks, native dead-lettering, retries.",
            ],
            [
                "Cache / limits",
                "Redis",
                "In-memory reads; atomic counters shared by all API replicas.",
            ],
            ["Scheduling", "Celery beat + croniter", "Standard cron expressions per source."],
            [
                "Observability",
                "prometheus_client, JSON logs",
                "Standard metrics and searchable logs.",
            ],
            [
                "Delivery",
                "Docker, Compose, GitHub Actions",
                "Reproducible builds and CI on every push.",
            ],
        ],
        [26 * mm, 40 * mm, W - 66 * mm],
    )
)

# ---------------- 6. Data model ----------------
s.append(PageBreak())
s.append(P("6. Data Model", h1))
s.append(
    table(
        [
            ["Entity", "Key fields", "Notes"],
            [
                "users",
                "id, email (unique), hashed_password, role, is_active, created_at",
                "role: viewer, editor.",
            ],
            [
                "data_sources",
                "id, name (unique), source_type, connection_config (JSON), schedule_cron, "
                "is_active, last_scheduled_at",
                "Password masked on output.",
            ],
            [
                "validation_rules",
                "id, data_source_id, name, rule_type, table_name, column_name, "
                "rule_config (JSON), severity, threshold, is_active",
                "Cascade with source.",
            ],
            [
                "check_runs",
                "id, data_source_id, status, trigger, started_at, finished_at, rule counts, "
                "error_message",
                "queued, running, completed, failed.",
            ],
            [
                "check_results",
                "id, run_id, rule_id, status, total_rows, failing_rows, observed_value, "
                "message, sample_failures (JSON), duration_ms",
                "passed, failed, error.",
            ],
            [
                "alerts",
                "id, rule_id, last_result_id, severity, status, message, occurrences, "
                "last_seen_at, acknowledged_at, resolved_at",
                "open, acknowledged, resolved.",
            ],
        ],
        [28 * mm, W - 72 * mm, 44 * mm],
    )
)
s.append(P("6.1 Check types", h2))
s.append(
    table(
        [
            ["Rule type", "Fails when", "Configuration"],
            ["not_null", "column IS NULL", "none"],
            ["unique", "duplicated non-null values exist", "none"],
            ["range", "value &lt; min or value &gt; max", "min and/or max"],
            ["accepted_values", "value not in the allowed list", "values: [...]"],
            ["regex", "value does not match the pattern", "pattern"],
            ["row_count", "table row count outside [min, max]", "min and/or max"],
            ["freshness", "newest timestamp older than the limit", "max_age_minutes"],
            ["custom_sql", "the query returns any rows", "sql (single SELECT/WITH)"],
        ],
        [32 * mm, W - 82 * mm, 50 * mm],
    )
)
s.append(P("6.2 Cache keys and queues", h2))
s.append(
    table(
        [
            ["Name", "Kind", "Purpose"],
            ["dq:cache:source_status:{id}", "Redis key, TTL 60 s", "Cached per-source status."],
            ["dq:cache:summary", "Redis key, TTL 60 s", "Cached dashboard summary."],
            ["dq:ratelimit:...", "Redis counter", "Fixed-window rate limiting (API and login)."],
            [
                "dq.tasks",
                "RabbitMQ durable queue",
                "Run, notification and scheduler tasks; dead-letters to dq.dlx.",
            ],
            [
                "dq.dead",
                "RabbitMQ durable queue",
                "Permanently failed tasks for inspection and replay.",
            ],
        ],
        [46 * mm, 38 * mm, W - 84 * mm],
    )
)

# ---------------- 7. API ----------------
s.append(PageBreak())
s.append(P("7. External Interface: REST API", h1))
s.append(
    P(
        f"Base path {mono('/api/v1')}; JSON bodies; {mono('Authorization: Bearer &lt;JWT&gt;')} "
        f"on every route except login. List endpoints accept {mono('limit')} (1-200) and "
        f"{mono('offset')} and return {mono('{items, total, limit, offset}')}. Interactive "
        f"documentation is served at {mono('/docs')}."
    )
)
s.append(
    table(
        [
            ["Method", "Path", "Role", "Codes"],
            ["POST", "/auth/login (form: username, password)", "anyone", "200, 401, 403, 429"],
            ["GET", "/auth/me", "any", "200, 401"],
            ["POST, GET, PATCH", "/users, /users/{id}", "editor", "200, 201, 403, 409, 422"],
            ["POST, GET", "/data-sources", "editor / any", "201, 200, 409, 422"],
            ["GET, PATCH, DELETE", "/data-sources/{id}", "any / editor", "200, 204, 404"],
            ["GET", "/data-sources/{id}/status (cached)", "any", "200, 404"],
            ["POST", "/data-sources/{id}/test-connection", "editor", "200"],
            ["POST", "/data-sources/{id}/runs", "editor", "202, 404, 409, 503"],
            ["GET", "/data-sources/{id}/runs", "any", "200"],
            ["GET", "/rules/types", "any", "200"],
            ["POST, GET", "/rules?data_source_id=", "editor / any", "201, 200, 404, 422"],
            ["GET, PATCH, DELETE", "/rules/{id}", "any / editor", "200, 204, 404, 422"],
            ["GET", "/rules/{id}/results", "any", "200"],
            ["GET", "/runs?status=, /runs/{id}", "any", "200, 404"],
            ["GET", "/alerts?status=&amp;severity=", "any", "200"],
            ["POST", "/alerts/{id}/acknowledge, /resolve", "editor", "200, 409"],
            ["GET", "/dashboard/summary (cached)", "any", "200"],
            ["GET", "/health, /ready, /metrics (no prefix)", "public", "200, 503"],
        ],
        [27 * mm, 66 * mm, 24 * mm, W - 117 * mm],
    )
)
s.append(Spacer(1, 6))
s.append(P("Error format (all non-2xx responses)", h2))
s.append(
    P(
        '{ "error": { "code": "validation_error", "message": "Request validation failed",<br/>'
        '&nbsp;&nbsp;"details": [ { "field": "body.threshold", "message": "..." } ],<br/>'
        '&nbsp;&nbsp;"request_id": "9f2c..." } }',
        code,
    )
)
s.append(Spacer(1, 4))
s.append(
    table(
        [
            ["Status", "code"],
            ["401 / 403", "unauthorized / forbidden"],
            ["404 / 409", "not_found / conflict"],
            ["422", "validation_error"],
            ["429", "rate_limited"],
            ["500 / 503", "internal_error / service_unavailable"],
        ],
        [30 * mm, W - 30 * mm],
    )
)

# ---------------- 8. Acceptance ----------------
s.append(PageBreak())
s.append(P("8. Acceptance Criteria and Traceability", h1))
s.append(
    P(
        f"Requirements are verified by 81 automated tests in {mono('tests/')}, run by CI on "
        "every push, plus a Docker end-to-end CI job. Queue and cache behaviour was "
        "additionally verified against live RabbitMQ and Redis instances."
    )
)
s.append(
    table(
        [
            ["Requirements", "Verified by"],
            [
                "FR-1 to FR-8",
                "test_auth.py: login, hashing, expired/forged/unsigned tokens, deactivation, "
                "viewer vs editor, user management, self-lockout, bootstrap, CLI",
            ],
            [
                "FR-9 to FR-13",
                "test_api.py: source validation, masking, conflicts, pagination, cascade, "
                "test-connection",
            ],
            [
                "FR-14 to FR-18",
                "test_checks.py: each check type, 12 invalid-config cases; test_api.py: rule CRUD",
            ],
            [
                "FR-19 to FR-22, FR-27",
                "test_api.py: full run, unreachable source, history; "
                "test_worker.py: duplicate delivery",
            ],
            [
                "FR-23 to FR-26",
                "test_worker.py: retry then fail, transient recovery, DLQ wiring, broker "
                "down returns 503; CI e2e-docker checks dq.dead exists",
            ],
            ["FR-28, FR-29", "test_scheduler.py: due, not due, inactive, no pile-up, beat task"],
            [
                "FR-30 to FR-33",
                "test_api.py: alert dedup, auto-resolve, transitions, webhook once per incident",
            ],
            [
                "FR-34 to FR-36",
                "test_cache_and_errors.py: miss then hit, invalidation on run, alert and "
                "rule changes; test_api.py: dashboard summary",
            ],
            [
                "FR-37, FR-38",
                "test_cache_and_errors.py: 404, 422, unknown route, 500 hides internals",
            ],
            [
                "NFR-6, NFR-9",
                "test_cache_and_errors.py: Redis outage fails open, API rate limit; "
                "test_auth.py: login rate limit",
            ],
            ["NFR-8", "test_auth.py: algorithm whitelist, production secret check"],
            ["NFR-13, NFR-14", "CI: lint, tests (85% floor), migrations up/down/up on PostgreSQL"],
            [
                "NFR-3, NFR-4, NFR-15",
                "CI e2e-docker: Postgres, RabbitMQ, Redis, API, worker and beat; demo run completes",
            ],
        ],
        [38 * mm, W - 38 * mm],
    )
)

s.append(P("9. Future Enhancements", h1))
s += bullets(
    [
        "Refresh tokens and a Redis token denylist for instant token revocation.",
        "Per-team ownership of data sources (multi-tenant isolation).",
        "Statistical anomaly detection on observed_value history.",
        "Additional connectors: Snowflake, BigQuery, MySQL.",
        "Encryption of stored connection credentials (KMS or a secrets manager).",
        "Result retention policy and monthly partitioning of check_results.",
        "A web dashboard and Grafana dashboards on top of the existing API and metrics.",
    ]
)

doc = SimpleDocTemplate(
    str(OUT),
    pagesize=A4,
    leftMargin=18 * mm,
    rightMargin=18 * mm,
    topMargin=18 * mm,
    bottomMargin=20 * mm,
    title="DQ Monitor: Software Requirements Specification",
    author="DQ Monitor",
    subject="Requirements",
)
doc.build(s, onFirstPage=on_page, onLaterPages=on_page)
print("wrote", OUT)
