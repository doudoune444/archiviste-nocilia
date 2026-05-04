---
description: Run adversarial code review on the current diff via the reviewer sub-agent
argument-hint: <ID>
---

The user wants a hostile review of ticket `$ARGUMENTS`.

Pre-flight (abort with reason on any fail):

1. Extract ticket ID strictly: must match `^[A-Z]+-[0-9]+$`. If not, abort.
2. Working tree clean: `git status --porcelain` is empty. Otherwise tell user to re-run `/impl` or commit pending work.
3. Diff non-empty vs `develop`: `git diff develop...HEAD --stat` is non-empty. Otherwise nothing to review.
4. Verify `specs/acceptance/$ARGUMENTS.md` and `specs/plans/$ARGUMENTS.md` exist.

Delegate to the `reviewer` sub-agent with this prompt:

> Adversarial review of ticket `$ARGUMENTS`. Read `specs/acceptance/$ARGUMENTS.md` and `specs/plans/$ARGUMENTS.md`. Diff: `git diff develop...HEAD`. Run lints and tests locally to confirm green. Hunt gaming patterns, spec violations, security issues, quality issues per your agent definition. Write findings to `specs/reviews/$ARGUMENTS.md` and commit it (`docs(review): $ARGUMENTS verdict <X>`). Verdict: APPROVE / REQUEST_CHANGES / BLOCK.

After the reviewer returns:

1. Verify the report was committed: `git log -1 --pretty=%s` should match `docs(review): $ARGUMENTS verdict <X>`. If not, commit it manually:
   ```bash
   git add specs/reviews/$ARGUMENTS.md
   git commit -m "docs(review): $ARGUMENTS verdict <X>"
   ```
2. Read `specs/reviews/$ARGUMENTS.md` and surface the verdict + HIGH severity findings to user.
3. APPROVE → tell user `/eval` (if RAG) then `/ship`.
4. REQUEST_CHANGES / BLOCK → tell user to fix and re-run `/review`.
