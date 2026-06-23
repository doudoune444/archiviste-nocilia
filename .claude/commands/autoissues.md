---
description: Autonomous lane — decompose approved PRDs into ready-for-agent issues, but only the ones specified well enough to be safe. Run in a SEPARATE session from your active work.
argument-hint: "[PRD issue numbers, optional]"
---

You are a **lean driver**. You decide which PRDs are safe to decompose autonomously,
then run `/to-issues` on each. **Protect your context** — keep only outcomes, not the
full PRD bodies, once a PRD is handled.

## The safety gate (this is the whole point)

`/to-issues` involves **seam judgment** — choosing where to slice the work. That
judgment is meant to be human. Automate it ONLY where the PRD has already made it
explicit. For each candidate PRD:

- **Decompose** it only if its body contains an explicit **Testing Decisions** section
  AND named seams / acceptance criteria. Then the slicing is already decided — you're
  executing, not judging.
- **Skip** any PRD that lacks those. Do not guess seams. Leave it for the human and
  record it as `#<N> → skipped: needs seams/Testing Decisions`.

## Loop

1. Find candidate PRDs:
   - If issue numbers were passed, use them.
   - Else: `gh issue list --state open --label prd --json number,title,body`. The `prd`
     label is the authoritative marker of a parent PRD. (Fall back to PRD structure only
     for legacy PRDs created before the label existed — and relabel those with `prd`.)
2. For each PRD, apply the safety gate above.
3. For a PRD that passes the gate: run `/to-issues` against it. It creates the
   vertical-slice issues and labels them `ready-for-agent`.
4. Record one line per PRD: `#<N> → created issues <list>` or `#<N> → skipped: <reason>`.
5. When all candidates are handled, report the table and stop.

## Why this stays manual-ish

This lane is deliberately conservative. The whole methodology front-loads alignment
into grilling + PRD writing. If you let underspecified PRDs auto-decompose, the
downstream `/autobuild` agents inherit ambiguous tests and drift. Skipping is the safe
default — a skipped PRD costs you five minutes later; a badly-sliced one costs a
wrong PR.

## Pair with /goal (optional)

```
/goal every approved PRD with a Testing Decisions section has been decomposed into ready-for-agent issues. Proof: each such PRD links to its child issues. Constraint: skip and never decompose a PRD that lacks Testing Decisions.
```

Then run `/autoissues`. Same caveat as autobuild: session-scoped, keep this terminal
open and separate from your active work.
