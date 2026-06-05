"""Database: schema creation, lightweight migrations, and the per-environment
reach references. Categories are stored as plain text (data, not an enum), so
the schema is config-agnostic — only the reference floor comes from config.
"""
import os
import sqlite3

from sentryrice.config import Config

SCHEMA = """
    -- One row per `sync` run. `issues.first_scan_id` points at the scan that
    -- first imported an issue, so "New" = first_scan_id == the latest scan id.
    CREATE TABLE IF NOT EXISTS scans (
        id         INTEGER PRIMARY KEY,
        started_at DATETIME DEFAULT (datetime('now')),
        days       INTEGER,
        note       TEXT
    );

    CREATE TABLE IF NOT EXISTS issues (
        id            INTEGER PRIMARY KEY,
        sentry_id     TEXT    UNIQUE NOT NULL,
        title         TEXT    NOT NULL,
        url           TEXT    NOT NULL,
        environment   TEXT    DEFAULT 'unknown',
        app           TEXT    DEFAULT 'unknown',
        status        TEXT    DEFAULT 'unresolved',
        last_seen     DATETIME,
        user_count    INTEGER DEFAULT 0,
        event_count   INTEGER DEFAULT 0,
        fetched_at    DATETIME DEFAULT (datetime('now')),
        first_scan_id INTEGER REFERENCES scans(id)
    );

    CREATE TABLE IF NOT EXISTS scores (
        id              INTEGER PRIMARY KEY,
        issue_id        INTEGER NOT NULL REFERENCES issues(id),
        reach           REAL    NOT NULL CHECK(reach BETWEEN 0 AND 10),
        impact_category TEXT    NOT NULL,
        impact          REAL    NOT NULL CHECK(impact BETWEEN 0 AND 10),
        confidence      REAL    NOT NULL CHECK(confidence BETWEEN 0 AND 10),
        effort          REAL    NOT NULL CHECK(effort BETWEEN 0.5 AND 10),
        rice_score      REAL    NOT NULL,
        reasoning       TEXT,
        code_findings   TEXT,
        scored_at       DATETIME DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS overrides (
        id         INTEGER PRIMARY KEY,
        issue_id   INTEGER NOT NULL REFERENCES issues(id),
        field      TEXT    NOT NULL CHECK(field IN ('reach','impact','confidence','effort')),
        ai_value   REAL    NOT NULL,
        your_value REAL    NOT NULL,
        reason     TEXT,
        created_at DATETIME DEFAULT (datetime('now')),
        UNIQUE(issue_id, field)
    );
"""


def connect(db_path: str) -> sqlite3.Connection:
    """Open a connection tuned for the fan-out write pattern."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db(config: Config) -> None:
    """Create the schema if absent and run lightweight migrations. Idempotent."""
    os.makedirs(os.path.dirname(config.db_path) or ".", exist_ok=True)
    conn = connect(config.db_path)
    try:
        conn.executescript(SCHEMA)
        # Migrations for pre-existing `issues` tables. Existing rows get NULL for
        # added columns, so they're never falsely "New".
        cols = {row[1] for row in conn.execute("PRAGMA table_info(issues)").fetchall()}
        if "status" not in cols:
            conn.execute("ALTER TABLE issues ADD COLUMN status TEXT DEFAULT 'unresolved'")
        if "first_scan_id" not in cols:
            conn.execute("ALTER TABLE issues ADD COLUMN first_scan_id INTEGER REFERENCES scans(id)")
        conn.commit()
    finally:
        conn.close()


def reference_volumes(conn, floor: float) -> dict:
    """Per-environment yardsticks reach is measured against.

    Returns ``{environment: (user_reference, event_reference)}`` where each is the
    busiest user/event volume *within that environment* (events measured over
    0-user background-job issues). Resolved issues are excluded; both floored by
    `floor` so a near-empty env can't make its one issue an automatic 10.
    """
    rows = conn.execute(
        """
        SELECT
            environment,
            COALESCE(MAX(user_count), 0),
            COALESCE(MAX(CASE WHEN COALESCE(user_count, 0) = 0
                              THEN event_count END), 0)
        FROM issues
        WHERE COALESCE(status, 'unresolved') <> 'resolved'
        GROUP BY environment
        """
    ).fetchall()
    return {
        env: (max(float(umax or 0), floor), max(float(emax or 0), floor))
        for env, umax, emax in rows
    }


def references_for(refs: dict, environment, floor: float) -> tuple:
    """The (user_ref, event_ref) for one environment, falling back to the floor."""
    return refs.get(environment, (floor, floor))
