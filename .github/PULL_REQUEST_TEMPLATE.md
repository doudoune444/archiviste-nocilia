<!-- Title: <type>(<scope>): #<issue> <subject>   e.g.  feat(gateway): #142 anonymous cookie identity -->

## Closes

Closes #<issue>

## Vertical slice

<!-- One or two lines: the end-to-end behavior this slice delivers. Mirror the issue's
     "What to build" — describe behavior through the layers, not a file list. -->

## User story

<!-- From the issue / PRD. Omit this section if the slice has none. -->
As a <role>, I want <capability>, so that <outcome>.

## What changed & why

<!-- The decision-rich summary. WHAT changed at a high level, plus the WHY behind any
     non-obvious choice — a tradeoff taken, a constraint, an alternative rejected.
     Not a restatement of the diff; the reviewer can read the diff. -->

## Acceptance criteria

<!-- Copy from the issue; tick what this PR actually meets. -->
- [ ] AC-1 — covered by `<test>`
- [ ] AC-2 — covered by `<test>`

## Tests (TDD)

- [ ] Behavior verified through public interfaces (integration-style), not implementation details
- [ ] Red → green → refactor followed — test written before the code that satisfies it
- [ ] Full suite green locally

## Self-review

- [ ] `.claude/rules/clean-code.md` — ≤40-line functions, no abbreviations, no dead/commented code, semantic DRY
- [ ] `.claude/rules/security.md` pinned decisions + auto-fail list respected (if `gateway/` / `workers/` / `infra/` touched)
- [ ] No workaround — any blocker logged in `docs/blockers.md`, never patched around
- [ ] No secrets in the diff

## Out of scope

<!-- What this PR deliberately does NOT do — deferred follow-ups, with issue refs if they exist. -->
