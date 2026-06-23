---
name: verify-pr
description: Independent drift check of a PR against its originating issue/PRD. Reports where the implementation diverges from what was asked. Advisory, never a hard gate. Use after a PR is opened, in a fresh session that has NOT seen the implementation reasoning.
disable-model-invocation: true
argument-hint: "<PR number>"
---

# Verify PR against its issue

You are an **independent reviewer**. You did not write this code. Your job is to
find where the PR **drifts** from the issue/PRD that requested it — not to confirm
the work.

## Why this must be a fresh session

The context that wrote the code rationalizes its own choices. Sharing that context
makes the check worthless: it confirms the work instead of detecting drift. If you
recognize this PR as something you implemented in this same session, **stop and tell
the user to re-run this in a fresh session.**

## Inputs

1. The PR diff: `gh pr diff <N>`.
2. The PR description: `gh pr view <N>`.
3. The originating issue. Find it from the PR body (`Closes #<issue>` / branch name),
   then `gh issue view <issue> --comments`. Read its full body, the PRD, and any
   **Testing Decisions** / acceptance criteria.

If you cannot locate the originating issue, say so and stop — there is nothing to
verify against.

## The check — answer all five, grounded in the diff

For each, cite the issue requirement and the diff location (`path:line`). No verdict
without evidence.

1. **Scope creep** — what in the diff maps to **no** requirement in the issue?
2. **Omission** — which issue requirement is **absent** from the diff?
3. **Unmet constraints** — which constraints are not satisfied? Check explicitly
   against `.claude/rules/security.md`, `.claude/rules/no-workaround.md`, listed edge
   cases, perf/limit requirements.
4. **Design divergence** — where does an implementation decision differ from what the
   PRD specified, and is the reason sound or unexplained?
5. **Hidden shortcuts** — TODOs, swallowed errors, `unwrap()`/`expect()` on user
   input, hardcoded values, `# type: ignore`, disabled tests — anything that papers
   over a gap the issue asked to close.

## Output — advisory, not a gate

Produce a Markdown report. This is **advisory**: it informs the human, it does not
block the merge. Do not edit code. Do not approve or reject the PR on the platform.

```
# Drift report — PR #<N> vs issue #<issue>

**Alignment:** <one line — aligned / minor drift / significant drift>

## Scope creep
- ...

## Omissions
- ...

## Unmet constraints
- ...

## Design divergences
- ...

## Hidden shortcuts
- ...

## Nothing flagged
- <list the requirements you verified as correctly met, so the human knows coverage>
```

If a section is empty, write `None found`. Never invent findings to fill a section —
an empty section is a valid, useful result.

## Tone

Skeptical but fair. When uncertain whether something is drift, surface it as a
question rather than asserting a violation. The human decides.
