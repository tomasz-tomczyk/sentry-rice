export const meta = {
  name: 'score-issues',
  description: 'Score a list of Sentry issues against the codebase, one sub-agent per issue (rubric-driven)',
  phases: [
    { title: 'Load', detail: 'read the issue list from /tmp/unscored.json' },
    { title: 'Score', detail: 'one sub-agent per issue: trace into code, decide judgment fields, upsert' },
  ],
}

// Paths baked in by `sentry-rice init-claude` from your config.
const RUBRIC = '__RUBRIC_PATH__'
const CODEBASE = '__CODEBASE_PATH__'
const CONFIG = '__CONFIG_PATH__'
const UPSERT = `sentry-rice --config ${CONFIG} upsert`

// args: { list?: string } — path to the JSON array of issues. Defaults to /tmp/unscored.json.
const LIST = (args && args.list) || '/tmp/unscored.json'

phase('Load')
const loaded = await agent(
  `Use the Read tool to read the file ${LIST} and return its exact contents as JSON — an array of Sentry issue objects, returned unchanged under an "issues" key.`,
  { label: 'load', schema: {
      type: 'object', required: ['issues'], additionalProperties: false,
      properties: { issues: { type: 'array', items: { type: 'object', additionalProperties: true } } },
  } }
)
const issues = loaded.issues || []
log(`Loaded ${issues.length} issues to score from ${LIST}`)
if (!issues.length) throw new Error(`no issues loaded from ${LIST}`)

function buildPrompt(it) {
  const scoreFile = `/tmp/score-${it.sentry_id}.json`
  return `Score ONE Sentry issue for the RICE tool.

1. Read the canonical scoring rubric at ${RUBRIC} and follow it exactly.
2. Trace this issue into the codebase at ${CODEBASE} (Grep/Glob/Read there).
3. Persist your result to ${scoreFile}, then run this command and confirm it prints a line starting with "Upserted":
   ${UPSERT} < ${scoreFile}

ISSUE (raw JSON — copy its metadata fields verbatim into your score file):
${JSON.stringify(it, null, 2)}

Return a one-line status: "OK ${it.sentry_id} <category> conf=<n> eff=<n>" on success, or "FAIL ${it.sentry_id} <reason>" if the upsert errored.`
}

phase('Score')
const results = await parallel(issues.map((it) => () =>
  agent(buildPrompt(it), { label: `score:${it.sentry_id}`, phase: 'Score', model: 'sonnet' })
))
const done = results.filter(Boolean)
const fails = done.filter((r) => typeof r === 'string' && r.startsWith('FAIL'))
log(`Scoring agents finished: ${done.length}/${issues.length} returned, ${fails.length} self-reported FAIL`)
return { attempted: issues.length, returned: done.length, fails }
