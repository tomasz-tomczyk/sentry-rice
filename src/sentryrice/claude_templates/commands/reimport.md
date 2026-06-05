---
description: Re-sync Sentry (last N days, all envs) — import new issues, refresh counts, prune stale — then score the new ones.
---

Re-import the last N days of Sentry issues for every configured project and
environment, then score everything new. Scoring is one Claude sub-agent per
issue, tracing into __PROJECT_NAME__'s codebase.

1. **Sync.** Imports new issues (per-env event floors), refreshes counts, prunes
   issues not seen in the window (keeping any with overrides), recomputes reach +
   RICE. Activate your sentry-rice virtualenv first, then:

   ```bash
   sentry-rice --config __CONFIG_PATH__ sync
   ```

   Report the printed summary (window counts, imported, pruned).

2. **Dump the unscored issues** the sync just created:

   ```bash
   sentry-rice --config __CONFIG_PATH__ dump /tmp/unscored.json
   ```

   If it reports 0 unscored, stop here — nothing new to score.

3. **Score them, one sub-agent per issue**, via the bundled workflow (it reads
   `/tmp/unscored.json`, follows the rubric at `__RUBRIC_PATH__`, traces into
   `__CODEBASE_PATH__`, and upserts each result):

   ```
   Workflow({ scriptPath: ".claude/workflows/score-issues.js" })
   ```

   This is a large fan-out (one agent per unscored issue) — confirm with the user
   before launching if there are many.

4. **Renormalise** once scoring completes (reach is per-environment relative):

   ```bash
   sentry-rice --config __CONFIG_PATH__ recompute
   ```

5. Restart the web UI if it's running, and report the new top issues.
