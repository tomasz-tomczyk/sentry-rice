# sentry-rice

[![PyPI](https://img.shields.io/pypi/v/sentry-rice.svg)](https://pypi.org/project/sentry-rice/)
[![Python](https://img.shields.io/pypi/pyversions/sentry-rice.svg)](https://pypi.org/project/sentry-rice/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**RICE-prioritise your Sentry issues — with AI scoring that traces each issue
into your codebase.** Every issue gets one number, so the stuff actually worth
fixing floats to the top. Browse it in a Sentry-styled web UI; override anything
you disagree with; resolve straight through to Sentry.

```
RICE  =  (Reach × Impact × Confidence) / Effort
```

- **Reach** and the final **RICE** are computed *deterministically* — recent event
  volume on a log curve, **relative to the busiest issue in the same environment**
  (so a spicy staging issue ranks alongside a spicy prod one), decayed by recency.
  Never AI-assigned.
- **Impact**, **Confidence**, **Effort** come from an AI agent that reads the error
  and **traces it into your actual code** before judging. Impact is fixed per
  category; confidence/effort come from what the trace finds.

Everything opinionated — categories, thresholds, Sentry projects, the scoring
rubric — lives in a single `config.yaml`. Nothing is baked to one org.

---

## Requirements

- **Python 3.11+**
- A **Sentry auth token** (`SENTRY_AUTH_TOKEN`, or `[auth] token` in `~/.sentryclirc`).
  Read scope is enough for syncing; resolving issues from the UI needs write scope.
- **[Claude Code](https://claude.com/claude-code)** — *only* for the AI scoring step.
  The deterministic engine, sync, web UI, overrides and resolve all work without it;
  AI scoring is the Claude-Code-native layer (see [AI scoring](#ai-scoring)).

## How you consume it

This is a **library you install + a `config.yaml` you own** — not a repo you fork.
Keep your own small repo with just your config, rubric, and `.claude/` scoring
commands; install the engine as a dependency and pull updates with a version bump
instead of a fork-merge. (Fork only if you want to change the engine itself.)

Install it from PyPI:

```bash
python -m venv .venv && source .venv/bin/activate
pip install sentry-rice
```

Pull engine updates later with `pip install -U sentry-rice` — no fork to maintain.

## Quickstart

```bash
# 1. Install, then grab the example config + rubric to start from
pip install sentry-rice
curl -O https://raw.githubusercontent.com/tomasz-tomczyk/sentry-rice/main/config.example.yaml
curl -O https://raw.githubusercontent.com/tomasz-tomczyk/sentry-rice/main/rubric.example.md
mv config.example.yaml config.yaml && mv rubric.example.md rubric.md
$EDITOR config.yaml      # set sentry.org, projects, categories, codebase_path

# 2. Pull issues from Sentry (creates the DB, imports, scores reach/RICE)
export SENTRY_AUTH_TOKEN=...        # or rely on ~/.sentryclirc
sentry-rice --config config.yaml sync

# 3. Browse
sentry-rice --config config.yaml serve      # http://127.0.0.1:5001
```

At this point issues are imported and have a deterministic reach, but no AI
judgment yet. To score them, set up the AI layer.

> Tip: set `SENTRY_RICE_CONFIG=config.yaml` once and drop the `--config` flag.

## AI scoring

Scoring is done by Claude Code sub-agents — one per issue — that read your rubric,
trace the issue into `project.codebase_path`, decide impact/confidence/effort, and
upsert the result. Reach is never sent by the agent; it's computed.

```bash
# Render the scoring commands into your repo's .claude/ (paths baked from config)
sentry-rice --config config.yaml init-claude .
```

This writes:

- `.claude/commands/reimport.md` — sync the last N days + score the new issues
- `.claude/commands/reclassify.md` — re-score everything against the current rubric
- `.claude/commands/score-issue.md` — score a single issue
- `.claude/workflows/score-issues.js` — the per-issue fan-out the commands launch

Then, inside Claude Code in your repo, run `/reimport`. It syncs, dumps the unscored
issues, fans out one agent per issue, and recomputes. Edit `rubric.md` to change how
everything is scored, then `/reclassify`.

**No Claude Code?** You can still use the tool: reach/RICE, the UI, overrides and
resolve all work. Score `impact_category`/`confidence`/`effort` yourself by piping
JSON to `sentry-rice upsert`, or via the override form in the UI.

## Configuration

A single YAML file. See [`config.example.yaml`](config.example.yaml) for the full
annotated schema. The shape:

| Section | What it sets |
|---|---|
| `project` | name, `codebase_path` (where agents trace), `rubric_file` |
| `sentry` | `org`, `region_url`, `projects` (id → app), `environments`, `prod_environments` |
| `thresholds` | `sync_days`, per-env `min_events` floors, `reference_floor`, `recency_decay`, `rice_bands` |
| `categories` | `name → { score 0–10, icon (lucide), color }` — impact is fixed per category |
| `ui.fix_prompt_template` | the "copy fix prompt" body (optional; `{sentry_id}` etc. interpolated) |
| `db.path` | SQLite path (default `<config dir>/db/rice.db`; or `RICE_DB_PATH`) |

## CLI

```
sentry-rice initdb                 create / migrate the database
sentry-rice sync [--days N]        pull Sentry, import new, prune stale, recompute
sentry-rice serve [--port 5001]    run the web UI
sentry-rice dump [--all] [PATH]    write issues to JSON for the scoring fan-out
sentry-rice recompute              re-derive reach + RICE for all issues
sentry-rice upsert [PATH]          upsert one scored issue (JSON; stdin if no PATH)
sentry-rice init-claude [DEST]     render the .claude scoring commands into DEST
```

## A worked example

[`examples/acme/`](examples/acme/) is a complete configuration for a fictional B2B
SaaS, "Acme Cloud" (all values made up) — 10 categories, three projects
(backend/web/worker), prod+staging+dev, and a matching `rubric.md`. Copy it as a
starting point.

## Development

```bash
pip install -e ".[dev]"
pytest          # 41 tests: config, db, scoring, store, sentry (mocked), web, cli
```

## Disclaimer

This is an independent, unofficial project. It is **not affiliated with, endorsed
by, or sponsored by** Functional Software, Inc. or the Sentry project. "Sentry" is
a trademark of its respective owner and is used here only to describe what this
tool interoperates with.

## License

MIT — see [LICENSE](LICENSE).
