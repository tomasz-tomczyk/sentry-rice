import json
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


def test_parse_next_cursor_extra_attributes_and_empty_cursor():
    # Extra/reordered attributes must not confuse the regex.
    link = ('<https://x/?cursor=0:200:0>; foo="bar"; rel="next"; results="true"; '
            'cursor="0:200:0"; baz="qux"')
    assert _parse_next_cursor(link) == "0:200:0"
    # An empty cursor on the next rel is still a (degenerate) cursor value.
    link2 = '<https://x/?cursor=>; rel="next"; results="true"; cursor=""'
    assert _parse_next_cursor(link2) == ""
    # Malformed header without a cursor attribute → no next.
    assert _parse_next_cursor('<https://x/>; rel="next"; results="true"') is None


def test_fetch_recent_issues_paginates_across_pages(config, monkeypatch):
    # Two pages: the first carries a next cursor, the second does not.
    pages = iter([
        ([{"shortId": "A-1", "lastSeen": "2026-06-10T00:00:00Z", "count": 1}],
         '<https://x/?cursor=0:100:0>; rel="next"; results="true"; cursor="0:100:0"'),
        ([{"shortId": "A-2", "lastSeen": "2026-06-10T00:00:00Z", "count": 1}], None),
    ])

    class FakeResp:
        def __init__(self, body, link):
            self._body = json.dumps(body).encode()
            self.headers = {"Link": link} if link else {}

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=60):
        body, link = next(pages)
        return FakeResp(body, link)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    out = sentry.fetch_recent_issues(config, "111", "tok", 7, environment="production")
    assert [it["shortId"] for it in out] == ["A-1", "A-2"]


def test_fetch_loop_retries_on_429(config, monkeypatch):
    import urllib.error

    calls = {"n": 0}
    sleeps = []
    monkeypatch.setattr(sentry.time, "sleep", lambda s: sleeps.append(s))

    class FakeResp:
        headers = {}

        def read(self):
            return json.dumps([{"shortId": "A-1", "lastSeen": "2026-06-10T00:00:00Z"}]).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=60):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {"Retry-After": "2"}, None)
        return FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    out = sentry.fetch_recent_issues(config, "111", "tok", 7, environment="production")
    assert [it["shortId"] for it in out] == ["A-1"]
    assert calls["n"] == 2 and sleeps == [2]


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


def test_fetch_recent_issues_swallows_404_for_unknown_env(config, monkeypatch):
    import urllib.error
    import pytest

    def boom(req, timeout=60):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", boom)
    # A 404 for a named environment (it doesn't exist in this project) → empty, no raise.
    assert sentry.fetch_recent_issues(config, "111", "tok", 7, environment="staging") == []
    # A 404 with no environment is a genuine error and propagates.
    with pytest.raises(urllib.error.HTTPError):
        sentry.fetch_recent_issues(config, "111", "tok", 7)


def test_collect_window_stores_app_name_not_project_dict(config, monkeypatch):
    # load_config normalises every project to {"name": ..., "codebase_path": ...},
    # so collect_window must store the app *name* string, not the whole dict —
    # otherwise the dict reaches the SQLite bind in import_window and blows up.
    def fake_fetch(cfg, pid, token, days, environment=None):
        if pid != "111" or environment != "production":
            return []
        return [{"shortId": "API-1", "title": "boom", "permalink": "https://x/1",
                 "userCount": 3, "count": 42, "lastSeen": "2026-06-10T00:00:00Z"}]

    monkeypatch.setattr(sentry, "fetch_recent_issues", fake_fetch)
    window = sentry.collect_window(config, "tok", 7)
    rec = window["production"]["API-1"]
    assert rec["app"] == "api"
    assert isinstance(rec["app"], str)


def test_sync_all_reconciles_resolved_before_prune(config, monkeypatch):
    # Two tracked issues. Both are still in the window (so prune keeps them), but
    # Sentry now reports STILL-1 as resolved → sync_all should reflect that locally.
    for sid in ("STILL-1", "DONE-1"):
        _seed(config, sid)

    def fake_window(cfg, token, days):
        rec = lambda: {"app": "api", "title": "t", "url": "u", "user_count": 1,
                       "event_count": 1, "last_seen": "2026-06-10T00:00:00Z"}
        return {"production": {"STILL-1": rec(), "DONE-1": rec()}}

    def fake_short_ids(cfg, project_id, query, token):
        # "is:resolved" feed reports DONE-1 resolved on Sentry.
        return {"DONE-1"} if "resolved" in query else set()

    monkeypatch.setattr(sentry, "collect_window", fake_window)
    monkeypatch.setattr(sentry, "fetch_short_ids", fake_short_ids)
    monkeypatch.setattr("sentryrice.store.recompute_all", lambda cfg: 0)

    result = sentry.sync_all(config, days=7, token="tok", log=lambda *a, **k: None)
    assert result["reconcile"]["newly_resolved"] == 1
    conn = connect(config.db_path)
    statuses = dict(conn.execute("SELECT sentry_id, status FROM issues").fetchall())
    conn.close()
    assert statuses["DONE-1"] == "resolved"
    assert statuses["STILL-1"] == "unresolved"


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
