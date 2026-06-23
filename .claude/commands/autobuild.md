---
description: Autonomous lane — drain the ready-for-agent issue queue, one fresh sub-agent per issue (TDD → PR). Run in a SEPARATE session from your active work.
argument-hint: "[issue numbers, optional — default: all ready-for-agent]"
---

You are a **lean driver**. You do NOT implement anything yourself. Your only job is
to dispatch issues to fresh sub-agents and track results. **Protect your context** —
it is the scarce resource here.

## Context discipline (non-negotiable)

- **Never** read source files, diffs, or test output into YOUR context.
- **Never** run `tdd` yourself. Every build happens inside a sub-agent whose context
  is discarded when it returns.
- After a sub-agent finishes, keep **only** one line: `#<issue> → <pr-url | blocked>`.
- Do not summarize code. Do not echo diffs. Do not paste sub-agent transcripts.

## Loop

1. Find the queue:
   - If issue numbers were passed as arguments, use them.
   - Else: `gh issue list --state open --label ready-for-agent --json number --jq '.[].number'`.
2. If the queue is empty → report the run table and stop.
3. Take the next issue. Spawn **one sub-agent** with the Agent tool,
   `isolation: "worktree"`, and this task:

   > Implement issue #<N> for this repo, autonomously, test-first. Follow the `tdd`
   > skill (red→green→refactor, vertical slices, integration tests through public
   > interfaces). The "approve the plan" checkpoint is satisfied by the issue's PRD
   > and its Testing Decisions — treat those as the approved test plan; do NOT pause.
   > If the issue has no clear behavior/Testing Decisions, do nothing and return
   > "blocked: underspecified". Apply `.claude/rules/`: clean-code.md, security.md,
   > no-workaround.md. On a real blocker, obey no-workaround.md — append to
   > docs/blockers.md and return "blocked: <reason>", never patch around. When green
   > and refactored, commit on a feature branch, push, and `gh pr create` with
   > `Closes #<N>` in the body. **Return ONLY one line:** `#<N> → <pr-url>` or
   > `#<N> → blocked: <reason>`. No prose, no diff.

4. Record the one-line result. Go to step 1.

## Sequential, on purpose

One issue at a time. No parallel fan-out here — that keeps worktrees from colliding
on shared seams without needing an independence analysis, and keeps the driver calm.
(For a parallel burst when you know the issues are independent, use the `parallel-tdd`
workflow instead.)

## Run unattended with /goal

This command drains the queue once. To keep it self-restarting until the queue is
truly empty, pair it with `/goal` (the evaluator re-checks after every turn). Paste:

```
/goal every issue labelled ready-for-agent has an open PR that closes it. Proof: `gh issue list --state open --label ready-for-agent --json number` returns []. Constraint: leave untouched any issue with no Testing Decisions; never invent scope.
```

Then run `/autobuild`. Remember: `/goal` only fires while this Claude Code session is
running and idle — keep this terminal open, and keep it SEPARATE from the session
where you work on PRs and PRDs.
