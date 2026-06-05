---
description: Re-score every tracked issue from scratch against the current rubric.
---

Re-classify **all** tracked (unresolved) issues using the current rubric at
`rubric.md` — use this after editing the rubric or the categories in
`config.yaml`, to roll the new guidance across the whole dataset. This does
NOT re-import from Sentry (run `/reimport` for that).

1. **Dump every unresolved issue** (not just unscored):

   ```bash
   .venv/bin/sentry-rice --config config.yaml dump --all /tmp/unscored.json
   ```

   Report the count — this is how many sub-agents the next step spawns. Confirm
   with the user before launching, since re-scoring everything is a large fan-out
   and overwrites existing AI scores (manual **overrides are preserved** — they
   live in a separate table and are layered on top).

2. **Re-score, one sub-agent per issue**, via the workflow:

   ```
   Workflow({ scriptPath: ".claude/workflows/score-issues.js" })
   ```

3. **Renormalise** when it finishes:

   ```bash
   .venv/bin/sentry-rice --config config.yaml recompute
   ```

4. Restart the web UI if running, and report how the ranking shifted.
