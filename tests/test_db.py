from sentryrice.db import init_db, connect, reference_volumes, references_for


def test_tables_created(config):
    init_db(config)
    conn = connect(config.db_path)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert tables == {"issues", "scores", "overrides", "scans"}


def test_issues_has_scan_tracking_columns(config):
    init_db(config)
    conn = connect(config.db_path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(issues)").fetchall()}
    conn.close()
    assert "first_scan_id" in cols and "status" in cols


def test_init_db_is_idempotent(config):
    init_db(config)
    init_db(config)   # second run must not raise
    conn = connect(config.db_path)
    n = conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
    conn.close()
    assert n == 0


def test_reference_volumes_floor_when_empty(config):
    init_db(config)
    conn = connect(config.db_path)
    refs = reference_volumes(conn, config.thresholds.reference_floor)
    conn.close()
    assert refs == {}   # no rows → no envs
    # references_for falls back to the floor for any unknown env.
    assert references_for(refs, "production", config.thresholds.reference_floor) == (10.0, 10.0)


def test_reference_volumes_per_env_max(config):
    init_db(config)
    conn = connect(config.db_path)
    conn.executescript("""
        INSERT INTO issues (sentry_id, title, url, environment, user_count, event_count)
        VALUES ('A','a','u','production', 500, 10),
               ('B','b','u','production',  20, 99),
               ('C','c','u','staging',      5, 30);
    """)
    conn.commit()
    refs = reference_volumes(conn, config.thresholds.reference_floor)
    conn.close()
    # production user ref = max user_count (500); event ref = max events of 0-user issues (none) -> floor 10
    assert refs["production"][0] == 500.0   # busiest user volume in prod
    assert refs["production"][1] == 10.0     # no 0-user issues → event ref floored
    # staging's only issue has 5 users, below the floor of 10 → lifted to the floor.
    assert refs["staging"] == (10.0, 10.0)
