from datetime import datetime, timedelta, timezone

import pytest

from sentryrice.db import connect
from sentryrice.store import upsert_score, recompute_all, dump_issues


def _payload(**over):
    base = {
        "sentry_id": "T-1", "title": "Boom", "url": "u",
        "environment": "production", "app": "api",
        "last_seen": datetime.now(timezone.utc).isoformat(),
        "user_count": 100, "event_count": 100,
        "impact_category": "billing", "confidence": 7.0, "effort": 2.0,
    }
    base.update(over)
    return base


def test_upsert_computes_reach_and_rice(config):
    info = upsert_score(config, _payload())
    assert info["reach"] > 0
    assert info["rice_score"] > 0
    conn = connect(config.db_path)
    row = conn.execute("SELECT reach, impact, confidence, effort, rice_score, impact_category "
                       "FROM scores").fetchone()
    conn.close()
    reach, impact, conf, eff, rice, cat = row
    assert impact == 8.0          # billing → 8 from config
    assert cat == "billing"
    assert rice == round(reach * 8.0 * 7.0 / 2.0, 2)


def test_upsert_ignores_supplied_reach(config):
    info = upsert_score(config, _payload(reach=9.9, user_count=50, event_count=50))
    # As the sole issue it's its own reference → base reach 10 × recency(today)=1.0.
    assert info["reach"] == 10.0   # NOT 9.9 from the payload


def test_upsert_unknown_category_raises(config):
    with pytest.raises(ValueError, match="Unknown impact_category"):
        upsert_score(config, _payload(impact_category="not_a_category"))


def test_recompute_all_redecays_reach(config):
    # Seed an issue last seen 25 days ago → recency 0.5.
    old = (datetime.now(timezone.utc) - timedelta(days=25)).isoformat()
    upsert_score(config, _payload(last_seen=old, user_count=900, event_count=900))
    n = recompute_all(config)
    assert n == 1
    conn = connect(config.db_path)
    reach = conn.execute("SELECT reach FROM scores").fetchone()[0]
    conn.close()
    assert reach == 5.0   # sole issue → ref=itself → base 10 × 0.5 recency


def test_upsert_missing_required_field_raises_valueerror(config):
    """A payload missing any required field raises ValueError naming the field(s),
    and no partial row is written to the DB."""
    from sentryrice.db import init_db
    init_db(config)  # ensure tables exist so we can assert counts

    for missing_key in ("sentry_id", "title", "url", "confidence", "effort", "impact_category"):
        p = _payload()
        del p[missing_key]
        with pytest.raises(ValueError, match=missing_key):
            upsert_score(config, p)
    # DB must remain empty — no partial rows from any of the above attempts.
    conn = connect(config.db_path)
    assert conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 0
    conn.close()


def test_upsert_multiple_missing_fields_all_named(config):
    """When several required fields are absent, the error names all of them."""
    p = {"impact_category": "billing"}  # missing sentry_id, title, url, confidence, effort
    with pytest.raises(ValueError) as exc_info:
        upsert_score(config, p)
    msg = str(exc_info.value)
    for field in ("sentry_id", "title", "url", "confidence", "effort"):
        assert field in msg


def test_dump_issues_unscored_vs_all(config, tmp_path):
    upsert_score(config, _payload(sentry_id="SCORED-1"))
    # An unscored issue (no score row).
    conn = connect(config.db_path)
    conn.execute("INSERT INTO issues (sentry_id, title, url, environment, app, event_count) "
                 "VALUES ('UNSCORED-1','u','url','production','api', 5)")
    conn.commit()
    conn.close()

    unscored = dump_issues(config, all_issues=False, out_path=str(tmp_path / "u.json"))
    every = dump_issues(config, all_issues=True, out_path=str(tmp_path / "a.json"))
    assert {r["sentry_id"] for r in unscored} == {"UNSCORED-1"}
    assert {r["sentry_id"] for r in every} == {"SCORED-1", "UNSCORED-1"}
