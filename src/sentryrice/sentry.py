"""Sentry integration — NO AI. Talks to the Sentry REST API directly (stdlib
urllib). Everything org/project/environment-specific comes from `Config`.

Pipeline (see `sync_all`): collect the last-N-days window per environment for
every project → open a scan → import new issues → refresh counts → prune issues
no longer seen → recompute reach/RICE. Also marks issues resolved on Sentry.

Auth: `SENTRY_AUTH_TOKEN` env, else the `[auth] token` in ~/.sentryclirc.
"""
import configparser
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from sentryrice.config import Config
from sentryrice.db import connect, init_db


def read_token() -> str:
    token = os.environ.get("SENTRY_AUTH_TOKEN")
    if token:
        return token.strip()
    cfg_path = os.path.expanduser("~/.sentryclirc")
    if os.path.exists(cfg_path):
        cfg = configparser.ConfigParser()
        cfg.read(cfg_path)
        if cfg.has_option("auth", "token"):
            return cfg.get("auth", "token").strip()
    raise RuntimeError(
        "No Sentry token. Set SENTRY_AUTH_TOKEN or add [auth] token to ~/.sentryclirc."
    )


def _api_request(url, token, method="GET", body=None):
    """Minimal Sentry REST call. JSON body for writes; returns parsed JSON (or {}).
    Raises urllib.error.HTTPError on non-2xx (e.g. 403 when the token can't write)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode()
    return json.loads(raw) if raw else {}


def resolve_on_sentry(config: Config, short_id, token):
    """Mark the issue with this shortId resolved on Sentry.

    The DB stores shortIds but the update endpoint is keyed by numeric group id,
    so resolve the shortId first, then PUT status=resolved. Returns the numeric
    id. Raises on failure (caller must NOT delete locally on failure)."""
    org, region = config.sentry.org, config.sentry.region_url
    info = _api_request(
        f"{region}/api/0/organizations/{org}/shortids/{urllib.parse.quote(short_id)}/", token)
    numeric = (info.get("group") or {}).get("id") or info.get("groupId")
    if not numeric:
        raise RuntimeError(f"Could not resolve a numeric issue id for {short_id}")
    _api_request(f"{region}/api/0/organizations/{org}/issues/{numeric}/", token,
                 method="PUT", body={"status": "resolved"})
    return numeric


def _parse_next_cursor(link_header):
    """Sentry paginates via a Link header; return the next cursor if more results."""
    if not link_header:
        return None
    for part in link_header.split(","):
        if 'rel="next"' in part and 'results="true"' in part:
            for kv in part.split(";"):
                kv = kv.strip()
                if kv.startswith('cursor="'):
                    return kv[len('cursor="'):-1]
    return None


def fetch_short_ids(config: Config, project_id, query, token):
    """Return the set of issue shortIds matching `query` for one project."""
    org, region, max_pages = config.sentry.org, config.sentry.region_url, config.sentry.max_pages
    short_ids = set()
    cursor = None
    base = f"{region}/api/0/organizations/{org}/issues/"
    for _ in range(max_pages):
        params = {"project": project_id, "query": query, "limit": "100", "statsPeriod": "90d"}
        if cursor:
            params["cursor"] = cursor
        req = urllib.request.Request(base + "?" + urllib.parse.urlencode(params),
                                     headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            issues = json.loads(resp.read().decode())
            link = resp.headers.get("Link")
        for it in issues:
            sid = it.get("shortId")
            if sid:
                short_ids.add(sid)
        cursor = _parse_next_cursor(link)
        if not cursor:
            break
    return short_ids


def fetch_recent_issues(config: Config, project_id, token, days, environment=None):
    """Issue dicts for one project last seen within `days`, newest first. When
    `environment` is given, counts come back scoped to that environment."""
    org, region, max_pages = config.sentry.org, config.sentry.region_url, config.sentry.max_pages
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    cursor = None
    base = f"{region}/api/0/organizations/{org}/issues/"
    for _ in range(max_pages):
        params = {"project": project_id, "query": "is:unresolved", "sort": "date",
                  "limit": "100", "statsPeriod": f"{days}d"}
        if environment:
            params["environment"] = environment
        if cursor:
            params["cursor"] = cursor
        req = urllib.request.Request(base + "?" + urllib.parse.urlencode(params),
                                     headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                issues = json.loads(resp.read().decode())
                link = resp.headers.get("Link")
        except urllib.error.HTTPError as e:
            # An environment that doesn't exist in this project 404s; treat it as
            # "no issues for that env" rather than failing the whole sync.
            if e.code == 404 and environment:
                return out
            raise
        stop = False
        for it in issues:
            last_seen = it.get("lastSeen")
            try:
                dt = datetime.fromisoformat(str(last_seen).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                dt = None
            if dt and dt < cutoff:
                stop = True
                break
            out.append(it)
        if stop:
            break
        cursor = _parse_next_cursor(link)
        if not cursor:
            break
    return out


def collect_window(config: Config, token, days):
    """Fetch every unresolved issue seen in the last `days`, across all projects
    and configured environments, with env-scoped counts.

    Returns ``{stored_env: {shortId: record}}``. Several Sentry env spellings can
    fold into one stored env, keeping whichever sighting has more events."""
    window = {}
    for pid, app in config.sentry.projects.items():
        for q_env, stored_env in config.env_queries():
            for it in fetch_recent_issues(config, pid, token, days, environment=q_env):
                sid = it.get("shortId")
                if not sid:
                    continue
                rec = {
                    "app": app,
                    "title": it.get("title") or it.get("culprit") or sid,
                    "url": it.get("permalink") or f"{config.sentry.region_url}/issues/{sid}/",
                    "user_count": int(it.get("userCount") or 0),
                    "event_count": int(it.get("count") or 0),
                    "last_seen": it.get("lastSeen"),
                }
                bucket = window.setdefault(stored_env, {})
                prev = bucket.get(sid)
                if prev is None or rec["event_count"] > prev["event_count"]:
                    bucket[sid] = rec
    return window


def start_scan(config: Config, days, note=None):
    """Open a `scans` row for this run and return its id. Issues imported during
    the run are stamped with it, so "New" = first_scan_id == latest scan id."""
    init_db(config)
    conn = connect(config.db_path)
    try:
        cur = conn.execute("INSERT INTO scans (days, note) VALUES (?, ?)", (days, note))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def import_window(config: Config, window, min_events, scan_id=None):
    """Insert issues from `collect_window` we don't already track. `min_events` is
    a per-stored-env event floor (absent → 0). Freshly-inserted issues are stamped
    with `scan_id` (re-seen issues keep their original scan via INSERT OR IGNORE)."""
    init_db(config)
    conn = connect(config.db_path)
    imported = {}
    try:
        existing = {r[0] for r in conn.execute("SELECT sentry_id FROM issues").fetchall()}
        for stored_env, issues in window.items():
            threshold = min_events.get(stored_env, 0)
            for sid, rec in issues.items():
                if sid in existing or rec["event_count"] <= threshold:
                    continue
                conn.execute("""
                    INSERT OR IGNORE INTO issues
                        (sentry_id, title, url, environment, app, status, last_seen, user_count, event_count, fetched_at, first_scan_id)
                    VALUES (?, ?, ?, ?, ?, 'unresolved', ?, ?, ?, datetime('now'), ?)
                """, (sid, rec["title"], rec["url"], stored_env, rec["app"],
                      rec["last_seen"], rec["user_count"], rec["event_count"], scan_id))
                existing.add(sid)
                imported[stored_env] = imported.get(stored_env, 0) + 1
        conn.commit()
        unscored = conn.execute("""
            SELECT COUNT(*) FROM issues i
            LEFT JOIN scores s ON s.issue_id = i.id
            WHERE s.id IS NULL
        """).fetchone()[0]
        return {"imported": imported, "imported_total": sum(imported.values()),
                "unscored_total": unscored}
    finally:
        conn.close()


def refresh_from_window(config: Config, window):
    """Refresh each tracked issue's counts/last_seen from the window (env-scoped).
    Issues not in the window are left for prune_stale."""
    conn = connect(config.db_path)
    refreshed = 0
    try:
        tracked = conn.execute("SELECT sentry_id, environment FROM issues").fetchall()
        for sid, env in tracked:
            rec = window.get(env, {}).get(sid)
            if rec is None:
                continue
            conn.execute(
                "UPDATE issues SET user_count = ?, event_count = ?, "
                "last_seen = COALESCE(?, last_seen) WHERE sentry_id = ?",
                (rec["user_count"], rec["event_count"], rec["last_seen"], sid))
            refreshed += 1
        conn.commit()
        return {"refreshed": refreshed}
    finally:
        conn.close()


def prune_stale(config: Config, seen_ids, keep_with_overrides=True):
    """Delete tracked issues not seen in the latest window. Their scores cascade.
    Issues carrying overrides (your disagreement data) are kept by default."""
    conn = connect(config.db_path)
    try:
        tracked = conn.execute("SELECT id, sentry_id FROM issues").fetchall()
        protected = {r[0] for r in conn.execute(
            "SELECT DISTINCT issue_id FROM overrides").fetchall()}
        deleted, kept_protected = 0, 0
        for issue_id, sid in tracked:
            if sid in seen_ids:
                continue
            if keep_with_overrides and issue_id in protected:
                kept_protected += 1
                continue
            conn.execute("DELETE FROM scores WHERE issue_id = ?", (issue_id,))
            conn.execute("DELETE FROM issues WHERE id = ?", (issue_id,))
            deleted += 1
        conn.commit()
        remaining = conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
        return {"deleted": deleted, "kept_protected": kept_protected, "remaining": remaining}
    finally:
        conn.close()


def reconcile(config: Config, resolved_ids):
    """Mark tracked issues resolved/unresolved based on the resolved id set."""
    init_db(config)
    conn = connect(config.db_path)
    try:
        tracked = [r[0] for r in conn.execute("SELECT sentry_id FROM issues").fetchall()]
        newly_resolved, reopened = 0, 0
        for sid in tracked:
            target = "resolved" if sid in resolved_ids else "unresolved"
            cur = conn.execute("SELECT status FROM issues WHERE sentry_id = ?", (sid,)).fetchone()[0]
            if cur != target:
                conn.execute("UPDATE issues SET status = ? WHERE sentry_id = ?", (target, sid))
                newly_resolved += target == "resolved"
                reopened += target == "unresolved"
        conn.commit()
        total_resolved = conn.execute(
            "SELECT COUNT(*) FROM issues WHERE status = 'resolved'").fetchone()[0]
        return {"tracked": len(tracked), "newly_resolved": newly_resolved,
                "reopened": reopened, "total_resolved": total_resolved}
    finally:
        conn.close()


def sync_all(config: Config, days=None, token=None, log=print):
    """Full sync pipeline. Returns a summary dict. `log` is called with progress
    strings (the CLI passes print; tests can pass a collector)."""
    from sentryrice.store import recompute_all

    days = days or config.thresholds.sync_days
    token = token or read_token()
    init_db(config)

    window = collect_window(config, token, days)
    seen_by_env = {env: len(issues) for env, issues in window.items()}
    seen_ids = {sid for issues in window.values() for sid in issues}
    log(f"Window (last {days}d): {seen_by_env}  unique_ids={len(seen_ids)}")

    scan_id = start_scan(config, days)
    min_events = dict(config.thresholds.min_events)
    imp = import_window(config, window, min_events, scan_id=scan_id)
    log(f"Import (scan #{scan_id}, min_events={min_events}): new={imp['imported']}  "
        f"unscored_now={imp['unscored_total']}")

    ref = refresh_from_window(config, window)
    log(f"Stats refresh: refreshed={ref['refreshed']}")

    pr = prune_stale(config, seen_ids)
    log(f"Prune (not seen in {days}d): deleted={pr['deleted']}  "
        f"kept_with_overrides={pr['kept_protected']}  remaining={pr['remaining']}")

    n = recompute_all(config)
    log(f"Recomputed reach + RICE for {n} scored issues.")
    return {"scan_id": scan_id, "window": seen_by_env, "import": imp,
            "refresh": ref, "prune": pr, "recomputed": n}
