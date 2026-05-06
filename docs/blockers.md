# Blockers Log

Append-only log of blockers encountered during implementation. See `.claude/rules/no-workaround.md`.

When an agent (or human) hits a blocker, append an entry below — never patch around the issue silently.

## Format

```
## YYYY-MM-DD — <ticket-id> — <one-line title>

- File : <path:line>
- Symptom : <exact error message or unexpected behavior>
- Why blocked : <what was tried, what fails, what is unknown>
- Suggested resolution : <new ADR? upstream issue? spec amendment? human decision needed?>
- Status : open | resolved (commit SHA / ticket ID)
```

## Entries

<!-- Append below this line. Most recent first. -->

## 2026-05-05 — FOUND-003 — agent permissions deny writes under `migrations/` and `tests/`

- File : `.claude/settings.json` `permissions`
- Symptom : `Write`/`Edit` tools fail with "File is in a directory that is denied by your permission settings" for:
  - `migrations/0002_schema.sql` (humain-only by design — expected)
  - `migrations/run.sh` (NOT humain-only per plan FOUND-003 H1 — UNEXPECTED)
  - `tests/migrations/run_tests.sh` (test harness extension — UNEXPECTED)
  - `tests/migrations/fixtures/*.txt` (new fixtures — UNEXPECTED)
- Why blocked : Plan FOUND-003 lists 8 files to touch under `migrations/` and `tests/migrations/`. Only `CHANGELOG.md` is in the agent's allow list. Permission scheme uses explicit `allow` rules; `tests/**` and the runner-script side of `migrations/**` were never added. Pre-existing `tests/migrations/run_tests.sh` (committed in FOUND-002) shows the path is expected to be agent-writable, but settings disagree.
- Suggested resolution :
  1. Add `Write(./tests/**)` + `Edit(./tests/**)` to allow list (test harness is agent-owned).
  2. Narrow migrations deny rule to SQL only : replace `Edit(./migrations/**)` / `Write(./migrations/**)` with `Edit(./migrations/*.sql)` / `Write(./migrations/*.sql)` so the runner script `run.sh` stays editable.
  3. After settings update, re-run `/impl FOUND-003`.
  Alternative : human applies the patches presented in the agent's report by hand.
- Status : resolved by PR #20 (chore(claude): widen impl permissions, merged 2026-05-06)
