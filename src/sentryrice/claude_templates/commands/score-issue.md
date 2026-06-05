---
description: Score (or re-score) a single Sentry issue by id/short-id/URL against the rubric.
argument-hint: <sentry short-id | issue URL>
---

Score one Sentry issue: **$ARGUMENTS**

The single-issue entry point. The scoring logic lives in one place — the rubric
at `__RUBRIC_PATH__` — which `/reimport` and `/reclassify` also use. Do this
inline (no workflow needed for one issue).

1. **Resolve the issue.** If `$ARGUMENTS` is a short-id you already track, read its
   stored metadata from the DB at `__DB_PATH__`:

   ```bash
   python -c "import sqlite3,json; c=sqlite3.connect('__DB_PATH__'); c.row_factory=sqlite3.Row; r=c.execute('SELECT sentry_id,title,url,environment,app,user_count,event_count,last_seen FROM issues WHERE sentry_id=? OR url LIKE ?', ('$ARGUMENTS','%$ARGUMENTS%')).fetchone(); print(json.dumps(dict(r)) if r else 'NOT_FOUND')"
   ```

   If `NOT_FOUND`, fetch it from Sentry (the Sentry MCP tools, or the REST API)
   to get title, url, environment, app, recent user/event counts and last_seen.
   For brand-new issues, prefer `/reimport` so counts and pruning stay consistent.

2. **Read the rubric** at `__RUBRIC_PATH__` and follow it exactly: trace the issue
   into the codebase at `__CODEBASE_PATH__`, then decide `impact_category`,
   `confidence`, `effort` and write Markdown `reasoning` + `code_findings`. Do NOT
   assign reach — it's computed for you.

3. **Upsert.** Write the full payload JSON to a temp file and pipe it in:

   ```bash
   sentry-rice --config __CONFIG_PATH__ upsert < /tmp/score-$ARGUMENTS.json
   ```

4. **Recompute** so per-environment reach re-levels:

   ```bash
   sentry-rice --config __CONFIG_PATH__ recompute
   ```

5. Report the resulting category, confidence, effort, computed reach and RICE.
