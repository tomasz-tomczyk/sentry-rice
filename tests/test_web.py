import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import sentryrice.web as web
from sentryrice.store import upsert_score
from sentryrice.sentry import start_scan


@pytest.fixture
def client(config):
    # Sole issue T-1: as its own reference base reach is 10; last_seen 25d ago
    # (recency 0.5) pins stored reach to a deterministic 5.0.
    upsert_score(config, {
        "sentry_id": "T-1", "title": "Test error", "url": "https://sentry.io/issues/T-1",
        "environment": "production", "app": "web",
        "last_seen": (datetime.now(timezone.utc) - timedelta(days=25)).isoformat(),
        "user_count": 2800, "event_count": 50,
        "impact_category": "billing", "confidence": 7.0, "effort": 2.0,
        "reasoning": "test reasoning", "code_findings": "Traced to web/src/Invoice.tsx.",
    })
    app = web.create_app(config)
    app.config["TESTING"] = True
    with app.test_client() as c:
        c._config = config
        yield c


def test_index_ok_and_shows_issue(client):
    h = client.get("/").get_data(as_text=True)
    assert "Test error" in h and "production" in h and "web" in h


def test_project_name_in_nav(client):
    assert "Test Project" in client.get("/").get_data(as_text=True)


def test_search_matches_title_and_id(client):
    assert b"Test error" in client.get("/?q=Test").data
    assert b"Test error" in client.get("/?q=T-1").data
    assert b"Test error" not in client.get("/?q=NOPE").data


def test_filter_by_min_reach(client):
    assert b"Test error" in client.get("/?min_reach=3").data
    assert b"Test error" not in client.get("/?min_reach=9").data


def test_override_saves_and_clears(client, config):
    conn = sqlite3.connect(config.db_path)
    iid = conn.execute("SELECT id FROM issues WHERE sentry_id='T-1'").fetchone()[0]
    conn.close()
    # Change confidence away from AI (7) → override recorded; ajax returns json.
    resp = client.post(f"/issues/{iid}/override", data={"confidence": "9.5"},
                       headers={"X-Requested-With": "fetch"})
    assert resp.status_code == 200 and resp.get_json()["eff_confidence"] == 9.5
    # Set back to AI value → override removed.
    client.post(f"/issues/{iid}/override", data={"confidence": "7.0"},
                headers={"X-Requested-With": "fetch"})
    conn = sqlite3.connect(config.db_path)
    n = conn.execute("SELECT COUNT(*) FROM overrides WHERE issue_id=? AND field='confidence'",
                     (iid,)).fetchone()[0]
    conn.close()
    assert n == 0


def test_override_out_of_range_rejected(client, config):
    conn = sqlite3.connect(config.db_path)
    iid = conn.execute("SELECT id FROM issues WHERE sentry_id='T-1'").fetchone()[0]
    conn.close()
    hdr = {"X-Requested-With": "fetch"}
    assert client.post(f"/issues/{iid}/override", data={"reach": "11"}, headers=hdr).status_code == 400
    assert client.post(f"/issues/{iid}/override", data={"effort": "0"}, headers=hdr).status_code == 400


def _iid(config):
    conn = sqlite3.connect(config.db_path)
    iid = conn.execute("SELECT id FROM issues WHERE sentry_id='T-1'").fetchone()[0]
    conn.close()
    return iid


def test_override_requires_xrw_header(client, config):
    iid = _iid(config)
    # No X-Requested-With → CSRF guard rejects (a cross-origin form can't set it).
    assert client.post(f"/issues/{iid}/override", data={"confidence": "9.5"}).status_code == 403
    # With the header → works.
    resp = client.post(f"/issues/{iid}/override", data={"confidence": "9.5"},
                       headers={"X-Requested-With": "fetch"})
    assert resp.status_code == 200


def test_override_rejects_cross_origin(client, config):
    iid = _iid(config)
    # Header present but Origin host doesn't match the request host → 403.
    resp = client.post(f"/issues/{iid}/override", data={"confidence": "9.5"},
                       headers={"X-Requested-With": "fetch", "Origin": "https://evil.example"})
    assert resp.status_code == 403


def test_resolve_requires_xrw_header(client, config, monkeypatch):
    monkeypatch.setattr(web, "read_token", lambda: "tok")
    monkeypatch.setattr(web, "resolve_on_sentry", lambda cfg, sid, tok: "123")
    iid = _iid(config)
    # No header → 403, and the row must NOT be deleted.
    assert client.post(f"/issues/{iid}/resolve").status_code == 403
    conn = sqlite3.connect(config.db_path)
    assert conn.execute("SELECT COUNT(*) FROM issues WHERE id=?", (iid,)).fetchone()[0] == 1
    conn.close()
    # With the header → works.
    resp = client.post(f"/issues/{iid}/resolve", headers={"X-Requested-With": "fetch"})
    assert resp.status_code == 200


def test_new_badge_and_filter(client, config):
    upsert_score(config, {
        "sentry_id": "NEW-1", "title": "Fresh boom", "url": "u",
        "environment": "production", "app": "api",
        "last_seen": datetime.now(timezone.utc).isoformat(),
        "user_count": 10, "event_count": 10,
        "impact_category": "billing", "confidence": 7.0, "effort": 2.0,
    })
    scan_id = start_scan(config, 7)
    conn = sqlite3.connect(config.db_path)
    conn.execute("UPDATE issues SET first_scan_id=? WHERE sentry_id='NEW-1'", (scan_id,))
    conn.commit()
    conn.close()

    full = client.get("/?env=all").get_data(as_text=True)
    assert "Fresh boom" in full and full.count("data-new-badge") == 1
    only = client.get("/?env=all&new=1").get_data(as_text=True)
    assert "Fresh boom" in only and "Test error" not in only
    part = client.get("/?partial=1&env=all&new=1").get_data(as_text=True)
    assert "Fresh boom" in part and "Test error" not in part


def test_partial_is_fragment_only(client):
    part = client.get("/?partial=1").get_data(as_text=True)
    assert 'id="results-count"' in part and "<tbody" in part
    assert "<html" not in part and 'id="filter-form"' not in part


def test_resolve_marks_then_deletes(client, config, monkeypatch):
    seen = {}
    monkeypatch.setattr(web, "read_token", lambda: "tok")
    monkeypatch.setattr(web, "resolve_on_sentry",
                        lambda cfg, sid, tok: seen.setdefault("sid", sid) or "123")
    conn = sqlite3.connect(config.db_path)
    iid = conn.execute("SELECT id FROM issues WHERE sentry_id='T-1'").fetchone()[0]
    conn.execute("INSERT INTO overrides (issue_id, field, ai_value, your_value, reason) "
                 "VALUES (?, 'reach', 5.0, 9.0, 'mine')", (iid,))
    conn.commit()
    conn.close()

    resp = client.post(f"/issues/{iid}/resolve", headers={"X-Requested-With": "fetch"})
    assert resp.status_code == 200 and seen["sid"] == "T-1"
    conn = sqlite3.connect(config.db_path)
    assert conn.execute("SELECT COUNT(*) FROM issues WHERE id=?", (iid,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM overrides WHERE issue_id=?", (iid,)).fetchone()[0] == 0
    conn.close()


def test_resolve_aborts_on_sentry_failure(client, config, monkeypatch):
    monkeypatch.setattr(web, "read_token", lambda: "tok")
    monkeypatch.setattr(web, "resolve_on_sentry",
                        lambda cfg, sid, tok: (_ for _ in ()).throw(RuntimeError("403")))
    conn = sqlite3.connect(config.db_path)
    iid = conn.execute("SELECT id FROM issues WHERE sentry_id='T-1'").fetchone()[0]
    conn.close()
    resp = client.post(f"/issues/{iid}/resolve", headers={"X-Requested-With": "fetch"})
    assert resp.status_code == 502
    conn = sqlite3.connect(config.db_path)
    assert conn.execute("SELECT COUNT(*) FROM issues WHERE id=?", (iid,)).fetchone()[0] == 1
    conn.close()


def test_about_and_disagreements(client):
    about = client.get("/about").get_data(as_text=True)
    assert "billing" in about and "Impact by category" in about
    assert client.get("/disagreements").status_code == 200
