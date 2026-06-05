# Score one Sentry issue — Acme Cloud rubric

You score **one** Sentry issue for a RICE prioritisation tool. You'll be given the
absolute path to the **Acme codebase** — do all tracing (Grep/Glob/Read) there.

You assign **three** fields: `impact_category`, `confidence`, `effort`. You do
**NOT** assign reach — it's computed from recent volume × recency, relative to the
busiest issue in the same environment. Pass `user_count`, `event_count`,
`last_seen`, and `environment` through accurately.

## Steps

1. **Read the error** from the title (use the Sentry MCP tools for a stacktrace
   only if the title isn't enough).
2. **Trace it into the code.** Cite concrete files (`path:line` where possible).
   Backend (`backend`) is the API; `web` is the dashboard SPA; `worker` is jobs.
3. **Decide the three fields.**
4. **Write** `reasoning` (one Markdown bullet per dimension) and `code_findings`
   (1–3 short paragraphs: root cause with backticked `file:line`, then the fix).

## impact_category — pick EXACTLY one (impact is fixed by category)

Pick by the issue's **actual user impact**, informed by the findings.

| category | when the issue is in… |
|---|---|
| `data_integrity` (10) | data written wrong, lost, or corrupted; migrations |
| `security` (9) | auth, permissions, tokens, data exposure |
| `payments` (8) | checkout, billing, invoices, subscriptions |
| `core_workflow` (7) | the primary product flow (projects, documents, the main job) |
| `integrations` (6) | webhooks, OAuth, third-party APIs |
| `account` (5) | signup, login UX, profile, team/settings |
| `ui_display` (4) | purely cosmetic / render-only glitches |
| `reporting` (3) | dashboards, analytics, exports |
| `other` (2) | a **real** bug that genuinely fits nothing above |
| `noise` (1) | expected / transient / self-healing / external / non-actionable |

- Use **`noise`** when the investigation concludes it's telemetry noise: routine
  websocket reconnects, expired-token logouts, retried transients, bot traffic,
  benign already-handled errors. Use it **even when the code lives in a high-impact
  area** — at that point it isn't a real defect.
- **`other`** is for a *genuine* uncategorisable bug — never a dumping ground.
- **Don't lower impact for a low-severity real bug.** Impact is category-fixed;
  transience is reflected by reach (recency decay), not by doctoring impact.

## confidence (0–10)

How sure you are this is a **real, actionable bug whose root cause you can
identify**. Start at 8, then adjust:

- **Thin-signal penalty (−1 to −2), per environment + type:** high-traffic prod
  clears the 50-event floor (dock only when very few distinct users);
  **staging/dev** import with no floor, so ≲10 events is thin (−1, or −2 if ~one
  user); **worker** jobs log 0 users — judge by `event_count`, never dock for 0
  users.
- **Root cause:** −1 if you could NOT locate it; +1 (max 10) if you pinpointed the
  exact failing code.

Low (1–4) for vague / unreproducible / external. Don't penalise for age — only the
last 7 days are imported.

## effort (0.5–10)

From the findings, not a guess: 0.5 = trivial one-liner/config. 3 = a module. 5 =
multi-file. 8 = migration / multi-service. 10 = unknown/architectural. Default 5
only when the code couldn't be located.

## Persist

Write a JSON object to the score file with EXACTLY these keys (copy the metadata
verbatim, add your decisions):

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

Run the upsert command you were given and confirm it prints a line starting with
`Upserted`. Never send `reach` or `rice_score` — they're computed.
