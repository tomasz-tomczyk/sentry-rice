"""Write paths over the DB: upsert a scored issue, recompute reach/RICE for all,
and dump issues to JSON for the scoring fan-out. All are config-driven (category
→ impact score, reference floor, recency-decay curve).
"""
import json
import sqlite3

from sentryrice.config import Config
from sentryrice.db import connect, init_db, reference_volumes, references_for
from sentryrice.scoring import compute_reach, compute_rice


def upsert_score(config: Config, payload: dict) -> dict:
    """Insert/update one issue + its score. Reach and RICE are computed here from
    the issue's volume/recency (env-relative); any `reach` in the payload is
    ignored. Impact is fixed by the issue's category. Returns the stored row info.
    """
    category = payload["impact_category"]
    scores = config.category_scores()
    if category not in scores:
        raise ValueError(
            f"Unknown impact_category '{category}'. Valid: {sorted(scores)}"
        )

    init_db(config)
    environment = payload.get("environment", "unknown")
    app = payload.get("app", "unknown")
    impact = float(scores[category])
    confidence = float(payload["confidence"])
    effort = max(0.5, float(payload["effort"]))
    issue_params = {**payload, "environment": environment, "app": app}

    floor = config.thresholds.reference_floor
    decay = config.thresholds.recency_decay
    rfloor = config.thresholds.recency_floor

    conn = connect(config.db_path)
    try:
        conn.execute("""
            INSERT INTO issues (sentry_id, title, url, environment, app, last_seen, user_count, event_count, fetched_at)
            VALUES (:sentry_id, :title, :url, :environment, :app, :last_seen, :user_count, :event_count, datetime('now'))
            ON CONFLICT(sentry_id) DO UPDATE SET
                title       = excluded.title,
                url         = excluded.url,
                environment = excluded.environment,
                app         = excluded.app,
                last_seen   = excluded.last_seen,
                user_count  = excluded.user_count,
                event_count = excluded.event_count,
                fetched_at  = excluded.fetched_at
        """, issue_params)

        # References derived AFTER the upsert so this issue counts toward its env max.
        user_ref, event_ref = references_for(reference_volumes(conn, floor), environment, floor)
        reach = compute_reach(
            payload.get("user_count", 0), payload.get("event_count", 0),
            payload.get("last_seen"), user_ref, event_ref, decay, rfloor,
        )
        rice_score = compute_rice(reach, impact, confidence, effort)

        issue_id = conn.execute(
            "SELECT id FROM issues WHERE sentry_id = ?", (payload["sentry_id"],)
        ).fetchone()[0]

        conn.execute("DELETE FROM scores WHERE issue_id = ?", (issue_id,))
        conn.execute("""
            INSERT INTO scores
                (issue_id, reach, impact_category, impact, confidence, effort, rice_score, reasoning, code_findings)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (issue_id, reach, category, impact, confidence, effort, rice_score,
              payload.get("reasoning", ""), payload.get("code_findings", "")))
        conn.commit()
    finally:
        conn.close()
    return {"sentry_id": payload["sentry_id"], "category": category, "app": app,
            "environment": environment, "reach": reach, "rice_score": rice_score}


def recompute_all(config: Config, now=None) -> int:
    """Re-derive reach + RICE for every scored issue (deterministic). Judgment
    fields are untouched. Run after re-importing metadata or to re-decay reach."""
    floor = config.thresholds.reference_floor
    decay = config.thresholds.recency_decay
    rfloor = config.thresholds.recency_floor
    conn = connect(config.db_path)
    conn.row_factory = sqlite3.Row
    try:
        refs = reference_volumes(conn, floor)
        rows = conn.execute("""
            SELECT s.id AS score_id, s.impact, s.confidence, s.effort,
                   i.user_count, i.event_count, i.last_seen, i.environment
            FROM scores s JOIN issues i ON i.id = s.issue_id
        """).fetchall()
        for r in rows:
            user_ref, event_ref = references_for(refs, r["environment"], floor)
            reach = compute_reach(r["user_count"], r["event_count"], r["last_seen"],
                                  user_ref, event_ref, decay, rfloor, now)
            rice = compute_rice(reach, r["impact"], r["confidence"], r["effort"])
            conn.execute("UPDATE scores SET reach = ?, rice_score = ? WHERE id = ?",
                         (reach, rice, r["score_id"]))
        conn.commit()
        return len(rows)
    finally:
        conn.close()


_DUMP_FIELDS = ("i.sentry_id, i.title, i.url, i.environment, i.app, "
                "i.user_count, i.event_count, i.last_seen")


def dump_issues(config: Config, all_issues=False, out_path="/tmp/unscored.json") -> list:
    """Write issues to `out_path` as JSON for the scoring fan-out. By default only
    unscored issues; with `all_issues` every unresolved issue (for re-scoring)."""
    conn = connect(config.db_path)
    conn.row_factory = sqlite3.Row
    try:
        if all_issues:
            query = (f"SELECT {_DUMP_FIELDS} FROM issues i "
                     "WHERE COALESCE(i.status, 'unresolved') <> 'resolved' "
                     "ORDER BY i.event_count DESC")
        else:
            query = (f"SELECT {_DUMP_FIELDS} FROM issues i "
                     "LEFT JOIN scores s ON s.issue_id = i.id "
                     "WHERE s.id IS NULL AND COALESCE(i.status, 'unresolved') <> 'resolved' "
                     "ORDER BY i.event_count DESC")
        rows = [dict(r) for r in conn.execute(query).fetchall()]
    finally:
        conn.close()
    with open(out_path, "w") as f:
        json.dump(rows, f)
    return rows
