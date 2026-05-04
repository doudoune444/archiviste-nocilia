---
description: Final pre-merge gate — verifies review APPROVE + eval PASS + CI green, then opens PR
argument-hint: <ID>
---

The user wants to ship ticket `$ARGUMENTS`.

PR base branch = `develop` (topology: feature → develop → main; main = release-only).

Pre-flight gates (ALL must pass — abort if any fails):

0. **ID validation**: `$ARGUMENTS` must match `^[A-Z]+-[0-9]+$`. Otherwise abort.
1. **Spec exists**: `specs/acceptance/$ARGUMENTS.md` exists and Status is `ready`.
2. **Plan exists**: `specs/plans/$ARGUMENTS.md` exists.
3. **Review APPROVED**: `specs/reviews/$ARGUMENTS.md` exists. Verdict line must match the regex `^## Verdict[[:space:]]*\nAPPROVE\b` (any whitespace tolerated, no `REQUEST_CHANGES` / `BLOCK`).
4. **CHANGELOG updated**: `git diff develop...HEAD -- CHANGELOG.md` is non-empty.
5. **Working tree clean**: `git status --porcelain` is empty (all agent commits done).
6. **Lints green**:
   - If `gateway/` touched: `cd gateway && cargo fmt --check && cargo clippy -- -D warnings`
   - If `workers/` touched: `cd workers && uv run ruff check . && uv run mypy src/`
7. **Tests green**:
   - If `gateway/` touched: `cd gateway && cargo test`
   - If `workers/` touched: `cd workers && uv run pytest`
8. **Contract green** (if `specs/openapi/` touched): gateway running locally — `curl -sf http://localhost:8080/healthz` first, then `uv run schemathesis run specs/openapi/gateway-to-workers.yml --base-url http://localhost:8080`. Port = gateway public port (workers on 8000 are internal-only).
9. **Eval green** (if retrieval/prompt/generation touched): latest run in `eval/runs/$ARGUMENTS-*.json` shows verdict PASS vs baseline.

If any gate fails, tell the user **exactly which** and stop.

If all pass:

1. Run `git log --oneline develop..HEAD` to show commits.
2. Run `git push` (current branch has upstream set; if not, tell user to set it — **never** force-push).
3. Open PR with `gh pr create --base develop` using a HEREDOC body:

```
## Summary
- <pull from CHANGELOG entry>

## Acceptance criteria
<copy from specs/acceptance/$ARGUMENTS.md>

## Review
<paste verdict + HIGH findings table from specs/reviews/$ARGUMENTS.md>

## Eval (if applicable)
<paste eval table from latest run>

## Test plan
- [ ] CI green
- [ ] Manual smoke test on Cloud Run preview
```

4. Return the PR URL to the user.

Never merge for the user. Merging is humain-only.
