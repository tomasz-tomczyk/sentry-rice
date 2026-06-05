# Score one Sentry issue (rubric)

You are scoring **one** Sentry issue for a RICE prioritisation tool. You will be
given the absolute path to a **codebase** — do all code tracing (Grep/Glob/Read)
inside it, NOT in the sentry-rice tool's own directory.

You assign **three** judgment fields: `impact_category`, `confidence`, `effort`.
You do **NOT** assign reach — it's computed deterministically from the issue's
recent volume and recency, **relative to the busiest issue in the same
environment** (so a spicy staging issue ranks with spicy prod issues; all envs
are treated equally). Pass `user_count`, `event_count`, `last_seen`, and
`environment` through accurately and reach takes care of itself.

## Steps

1. **Read the error** from the title. If you need the stacktrace/culprit to
   locate it, you MAY use the Sentry MCP tools — but only if the title isn't enough.
2. **Trace it into the code.** Grep/Glob/Read to find the implicated module(s) and
   likely root cause. Cite concrete files (`path:line` where possible).
3. **Decide the three fields** (below).
4. **Write** `reasoning` and `code_findings` as GitHub-flavored Markdown (the UI
   renders them). `reasoning`: a short bullet list, one per dimension. `code_findings`:
   1–3 short paragraphs — root cause (with backticked `file:line`), then the fix.

## impact_category — pick EXACTLY one

The categories and their fixed numeric impact are defined in your `config.yaml`.
Pick by the issue's **actual user impact**, informed by the findings — not merely
the domain the code lives in.

- Map the issue to the configured category that best matches what it harms when
  it breaks (e.g. a payments error → your billing-like category; an auth bypass →
  your security-like category).
- Use your lowest "noise" category when the investigation concludes it's telemetry
  noise / non-functional / self-healing / expected / external — routine websocket
  reconnects, expired-token logouts, retried transients, bot traffic, benign
  already-handled errors. Use it **even when the code lives in a high-impact area**
  — at that point it isn't a real defect. Be willing to call noise noise.
- Use a generic "other" category only for a *genuine* problem that fits nothing
  else — never as a dumping ground for noise.
- **Don't lower impact for a low-severity real bug.** Impact is category-fixed;
  transience is reflected by the computed reach (recency decay), not by impact.

## confidence (0–10)

How sure you are this is a **real, actionable bug whose root cause you can
identify** — not a one-off or false alarm. Start at 8, then adjust:

- **Thin-signal penalty (−1 to −2), judged for the issue's environment and type:**
  - **High-traffic prod** issues that clear your import event-floor are already
    established — only dock when they touch very few distinct users.
  - **Low-traffic envs** (staging/dev) imported with no floor: an issue firing only
    a handful of times (≲10 events) is genuinely thin → −1, or −2 if also ~one user.
  - **Background jobs** (0 logged-in users by nature): judge volume by `event_count`,
    NOT `user_count`. Never dock a worker just for having 0 users.
- **Root cause:** −1 if you could NOT locate it in the code; +1 (max 10) if you
  pinpointed the exact failing code.

Low (1–4) for vague / unreproducible / external / third-party. **Don't penalise for
"age"** — transience is judged from low volume, not from `last_seen`.

## effort (0.5–10)

Developer effort to fix, from the findings — not a guess. 0.5 = trivial one-liner /
config / copy. 3 = moderate logic change in one module. 5 = multi-file logic. 8 = DB
migration or multi-service change. 10 = unknown/architectural. Default 5 only when
the code couldn't be located.

## Persist

Write a single JSON object to the score file you were given, with EXACTLY these
keys — copy the issue-metadata fields verbatim, add your three decisions plus the
two prose fields:

```json
{
  "sentry_id": "...", "title": "...", "url": "...",
  "environment": "...", "app": "...", "last_seen": "...",
  "user_count": 0, "event_count": 0,
  "impact_category": "<your choice>",
  "confidence": 0, "effort": 0,
  "reasoning": "<markdown>", "code_findings": "<markdown>"
}
```

Then run the upsert command you were given (it reads that file) and confirm it
prints a line starting with `Upserted`. Reach and RICE are computed by the upsert —
never send `reach` or `rice_score`.
