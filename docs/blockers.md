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

## 2026-05-18 — INFRA-002 — PR-d: `transformers` cannot be dropped from runtime while `chunker.py` imports `AutoTokenizer`

- File : `workers/src/archiviste_workers/ingest/chunker.py:8`
- Symptom : `chunker.py` imports `from transformers import AutoTokenizer` and calls `AutoTokenizer.from_pretrained("BAAI/bge-m3")` to build the LangChain text splitter. The plan (PR-d "Files to touch") does NOT list `chunker.py` as a file to touch, yet mandates dropping `transformers>=4.45` from `[project.dependencies]`. Removing `transformers` from runtime deps while `chunker.py` imports it would cause `ImportError` at boot in the ingest path.
- Why blocked : The "Files to touch" list is the authoritative scope. Modifying `chunker.py` would be out-of-scope piggyback. Keeping `transformers` in runtime deps is inconsistent with the plan's stated goal. The architect left the ingest tokenizer path unresolved — the embedder swap (mistral-embed) does not change the chunking tokenizer.
- Suggested resolution :
  1. Keep `transformers>=4.45` in `[project.dependencies]` for V1 (ingest still needs it for the chunker tokenizer). Drop only `sentence-transformers>=3.3` which is purely the BGE-M3 embedder wrapper. Create a follow-up ticket (ING-016 or chunker-swap) to replace `AutoTokenizer` with a `tiktoken`-based or pure-Python splitter once Mistral tokenizer support is confirmed.
  2. OR: amend PR-d scope to also touch `chunker.py` (replace `AutoTokenizer.from_pretrained` with `MistralTokenizer` or fall back to character-based splitting), and update "Files to touch" in the plan.
  3. Option 1 is the minimal-risk path: `sentence-transformers` is ~2 GiB (model weights download), while `transformers` alone without `torch` is ~100 MB (tokenizer only, no model load). Image size goal is achievable with partial drop.
- Status : open — awaiting human decision before applying any resolution

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
