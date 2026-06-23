export const meta = {
  name: 'parallel-tdd',
  description: 'Analyze ready issues for independence, then implement the independent set in parallel via TDD (each in an isolated worktree), serializing dependent chains. Opens one PR per issue.',
  whenToUse: 'After to-issues has produced vertical-slice issues. Run in its own session (S3). Reset before verify-pr/debrief (S4).',
  phases: [
    { title: 'Independence', detail: 'one agent maps which issues can run in parallel vs must serialize' },
    { title: 'Build', detail: 'one TDD agent per issue, isolated worktree, opens a PR' },
  ],
}

// args: optional array of issue numbers. If omitted, the analyzer discovers
// `ready-for-agent` issues itself.
const requested = Array.isArray(args) ? args : null

const INDEPENDENCE_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['parallel', 'chains', 'reasoning'],
  properties: {
    parallel: {
      type: 'array',
      description: 'Issue numbers with no file/seam overlap and no dependency on each other — safe to run all at once.',
      items: { type: 'integer' },
    },
    chains: {
      type: 'array',
      description: 'Each inner array is a dependent sequence that MUST run in order (shared seam, or B needs A merged first).',
      items: { type: 'array', items: { type: 'integer' } },
    },
    reasoning: {
      type: 'string',
      description: 'Why each grouping — name the shared files/seams that forced serialization.',
    },
  },
}

const BUILD_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['issue', 'status', 'summary'],
  properties: {
    issue: { type: 'integer' },
    status: { type: 'string', enum: ['pr_opened', 'blocked', 'failed'] },
    pr: { type: 'string', description: 'PR URL if opened, else empty string.' },
    summary: { type: 'string', description: 'One line: what shipped, or why blocked/failed.' },
  },
}

phase('Independence')

const discoveryClause = requested
  ? `Analyze exactly these issues: ${requested.join(', ')}.`
  : `Discover the issues to build: \`gh issue list --state open --label ready-for-agent --json number,title,body --jq '.'\`. Use every one returned.`

const groups = await agent(
  `You are planning a parallel build. ${discoveryClause}

For each issue, read its full body and PRD (\`gh issue view <n>\`). Determine which issues
can be implemented concurrently and which must be serialized.

Two issues are NOT parallel-safe if they touch the same module/seam/file, or one depends
on the other being merged first. When unsure, serialize — a false "parallel" causes
merge contradictions; a false "serial" only costs time.

Return the parallel set and the dependent chains.`,
  { phase: 'Independence', schema: INDEPENDENCE_SCHEMA, label: 'independence-analysis' },
)

if (!groups) {
  log('Independence analysis failed — aborting.')
  return { error: 'independence analysis returned null' }
}

log(`Parallel: [${groups.parallel.join(', ')}] · Chains: ${groups.chains.map(c => c.join('→')).join(' | ') || 'none'}`)
log(groups.reasoning)

phase('Build')

function buildPrompt(issueNumber) {
  return `Implement issue #${issueNumber} for this repo, autonomously, test-first.

Follow the TDD skill at ~/.claude/skills/tdd/SKILL.md: vertical slices via tracer
bullets, red → green → refactor, one behavior at a time. Tests verify behavior through
public interfaces (integration-style), never implementation details.

You are running WITHOUT a human in the loop. The TDD skill's "get user approval on the
plan" step is satisfied by the issue's PRD and its Testing Decisions / acceptance
criteria — treat those as the approved test plan. Do NOT pause for approval. If the
issue lacks a clear behavior to test, set status "blocked" rather than inventing scope.

Apply the project rules in .claude/rules/: clean-code.md (≤40-line functions, no
abbreviations, no dead code, semantic DRY), security.md (the pinned decisions and
auto-fail list), no-workaround.md. If you hit a real blocker, obey no-workaround.md:
STOP, append an entry to docs/blockers.md, set status "blocked" — never patch around.

When all tests pass and the code is refactored, commit on a feature branch, push, and
open a PR with \`gh pr create\` whose body contains \`Closes #${issueNumber}\`. Return
the PR URL.`
}

function buildIssue(issueNumber) {
  return agent(buildPrompt(issueNumber), {
    phase: 'Build',
    schema: BUILD_SCHEMA,
    isolation: 'worktree',
    label: `tdd:#${issueNumber}`,
  })
}

const parallelResults = await parallel(groups.parallel.map(n => () => buildIssue(n)))

const chainResults = []
for (const chain of groups.chains) {
  for (const issueNumber of chain) {
    chainResults.push(await buildIssue(issueNumber))
  }
}

const all = [...parallelResults, ...chainResults].filter(Boolean)
const opened = all.filter(r => r.status === 'pr_opened')
const blocked = all.filter(r => r.status !== 'pr_opened')

log(`Done. ${opened.length} PR(s) opened, ${blocked.length} blocked/failed.`)

return {
  opened: opened.map(r => ({ issue: r.issue, pr: r.pr, summary: r.summary })),
  blocked: blocked.map(r => ({ issue: r.issue, status: r.status, summary: r.summary })),
  next: 'Reset session, then run /verify-pr and /debrief per opened PR (S4).',
}
