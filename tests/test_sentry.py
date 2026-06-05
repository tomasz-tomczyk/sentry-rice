from datetime import datetime, timezone

import sentryrice.sentry as sentry
from sentryrice.db import connect
from sentryrice.sentry import (
    _parse_next_cursor, start_scan, import_window, prune_stale, reconcile,
    resolve_on_sentry,
)
from sentryrice.store import upsert_score


def _seed(config, sentry_id, env="production"):
    upsert_score(config, {
        "sentry_id": sentry_id, "title": sentry_id, "url": "u",
        "environment": env, "app": "api",
        "last_seen": datetime.now(timezone.utc).isoformat(),
        "user_count": 100, "event_count": 100,
        "impact_category": "billing", "confidence": 7.0, "effort": 2.0,
    })


def test_parse_next_cursor():
    link = ('<https://x/?cursor=0:0:1>; rel="previous"; results="false"; cursor="0:0:1", '
            '<https://x/?cursor=0:100:0>; rel="next"; results="true"; cursor="0:100:0"')
    assert _parse_next_cursor(link) == "0:100:0"
    link2 = '<https://x/?cursor=0:100:0>; rel="next"; results="false"; cursor="0:100:0"'
    assert _parse_next_cursor(link2) is None
    assert _parse_next_cursor(None) is None


def test_import_window_stamps_first_scan_id_only_on_fresh_inserts(config):
    def rec(events):
        return {"app": "api", "title": "t", "url": "u", "user_count": 1,
                "event_count": events, "last_seen": datetime.now(timezone.utc).isoformat()}

    scan1 = start_scan(config, 7)
    import_window(config, {"production": {"A": rec(200), "B": rec(200)}}, {"production": 100}, scan_id=scan1)
    scan2 = start_scan(config, 7)
    import_window(config, {"production": {"A": rec(200), "C": rec(200)}}, {"production": 100}, scan_id=scan2)

    conn = connect(config.db_path)
    first = dict(conn.execute("SELECT sentry_id, first_scan_id FROM issues").fetchall())
    conn.close()
    assert scan2 > scan1
    assert first["A"] == scan1 and first["B"] == scan1   # re-seen keeps original
    assert first["C"] == scan2                            # only C is new in scan 2


def test_prune_stale_keeps_overrides(config):
    for sid in ("KEEP-1", "GONE-1", "OVR-1"):
        _seed(config, sid)
    conn = connect(config.db_path)
    ovr = conn.execute("SELECT id FROM issues WHERE sentry_id='OVR-1'").fetchone()[0]
    conn.execute("INSERT INTO overrides (issue_id, field, ai_value, your_value, reason) "
                 "VALUES (?, 'reach', 5.0, 9.0, 'mine')", (ovr,))
    conn.commit()
    conn.close()

    result = prune_stale(config, {"KEEP-1"})
    assert result["deleted"] == 1 and result["kept_protected"] == 1 and result["remaining"] == 2
    conn = connect(config.db_path)
    survivors = {r[0] for r in conn.execute("SELECT sentry_id FROM issues").fetchall()}
    conn.close()
    assert survivors == {"KEEP-1", "OVR-1"}


def test_reconcile_marks_resolved_and_reopens(config):
    for sid in ("A-1", "A-2", "A-3"):
        _seed(config, sid)
    r = reconcile(config, {"A-2"})
    assert r["newly_resolved"] == 1 and r["total_resolved"] == 1
    r2 = reconcile(config, {"A-3"})
    assert r2["newly_resolved"] == 1 and r2["reopened"] == 1


def test_resolve_on_sentry_resolves_shortid_then_puts(config, monkeypatch):
    calls = []

    def fake_api(url, token, method="GET", body=None):
        calls.append((method, url, body))
        if "/shortids/" in url:
            return {"group": {"id": "99887"}}
        return {}

    monkeypatch.setattr(sentry, "_api_request", fake_api)
    numeric = resolve_on_sentry(config, "ACME-BACKEND-3516", "tok")
    assert numeric == "99887"
    puts = [c for c in calls if c[0] == "PUT"]
    assert len(puts) == 1 and puts[0][1].endswith("/issues/99887/") and puts[0][2] == {"status": "resolved"}
    # The org from config is in the URL.
    assert "/organizations/test-org/" in puts[0][1]
