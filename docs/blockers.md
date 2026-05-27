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

## 2026-05-27 — INFRA-002 — cloudflare provider 4.52 dropped `bot_fight_mode` arg + deprecated resources

- File : `infra/terraform/cloudflare.tf:63` (bot_fight_mode), L33 (`value` → `content`), L71 (`cloudflare_rate_limit` deprecated 11+ months past EOL)
- Symptom : `terraform apply` fails parse with `Error: Unsupported argument — An argument named "bot_fight_mode" is not expected here.` on `cloudflare_zone_settings_override.nocilia_fr`. Provider installé : `cloudflare/cloudflare v4.52.7` (latest 4.x, pinned `~> 4` in versions.tf). Warnings additionnels : `cloudflare_record.value` deprecated → `content` ; `cloudflare_rate_limit` deprecation phase ended June 15th 2025 (warning text says "still fully supported during the phase" mais cette phase est terminée depuis 11+ mois en mai 2026 — runtime apply incertain).
- Why blocked : INFRA-002 PR-b (cloudflare.tf) écrit contre provider CF ~4.30-ish où `bot_fight_mode` était dans `cloudflare_zone_settings_override`. Provider 4.5x l'a déplacé vers `cloudflare_bot_management` (paid plans) ou exige toggle manuel UI sur plan Free. Code mergé main ne s'applique plus. `cloudflare_rate_limit` deprecated post-EOL = runtime apply à risque.
- Suggested resolution :
  1. **Option A (retenue)** — branche `fix/INFRA-002-cf-provider-compat` :
     - retirer `bot_fight_mode` du settings_override → CF UI step 11 (manuel one-shot)
     - fix `value` → `content`
     - drop `cloudflare_rate_limit.archiviste_fr` resource → CF UI step 12 (manuel one-shot, V2 SEC-002 migrera vers app-level tower_governor + Redis qui rendra CF perimeter rule redundant)
     - documenter steps 11 + 12 dans `docs/runbook/bootstrap-gcp.md`
     - Spec AC-8 amendée scope : bot_fight + rate-limit via runbook humain au lieu de Terraform.
  2. Option B — pin provider `= 4.40.0`. Fragile, retarde inéluctable migration.
  3. Option C — upgrade provider `~> 5` : refactor lourd, hors-scope V1.
- Status : open

## 2026-05-25 — OPS-002 — aws-lc-sys requires NASM on Windows (x86_64-pc-windows-msvc)

- File: `gateway/Cargo.toml:25`
- Symptom: `cargo build` panics in `aws-lc-sys v0.41.0` build script: `NASM command not found! Build cannot continue.` on `x86_64-pc-windows-msvc` (Windows 11, dev machine). NASM is not installed in the worktree environment.
- Why blocked: `jsonwebtoken = { version = "10", default-features = false, features = ["aws_lc_rs"] }` was applied per AC-1. `aws_lc_rs` chains `aws-lc-rs` → `aws-lc-sys` which on Windows MSVC requires NASM for assembly crypto routines. The dev environment does not have NASM (`nasm.exe`) on PATH. The build cannot proceed without it. This is a platform/toolchain gap, not a code issue.
- Suggested resolution:
  1. **Install NASM on Windows dev machine**: download from https://www.nasm.us/ (or `winget install nasm`), add to PATH, re-run `cargo build`. This is the clean path — OQ-3 in the plan was scoped to CI (ubuntu-latest has NASM), but Windows dev also needs it.
  2. **Alternative — use `aws_lc_rs` with `fips` = false and pre-generated bindings**: `aws-lc-rs` provides pre-generated bindings for common targets (`AWS_LC_SYS_PREBUILT_NASM=1` env var or the `prebuilt-nasm` feature) which avoids NASM assembly compilation. Check if `aws-lc-sys` 0.41.0 supports `AWS_LC_SYS_PREBUILT_NASM=1` on Windows — if yes, this can be documented as a dev-env workaround without changing the `Cargo.toml` feature set.
  3. **Alternative — CI-only verification**: if the Windows dev build is not a CI requirement (CI runs Ubuntu), the blocker is local-only. Human decision: accept "build on Windows dev requires NASM" as an environment pre-req (document in `docs/runbook/` or `README`) and proceed. CI ubuntu-latest already has NASM.
- Status: resolved — NASM installed via `winget install nasm`, found at `C:\Users\bapti\AppData\Local\bin\NASM\nasm.exe`. `cargo build` passes. CI (ubuntu-latest) already has NASM. `use_pem` feature also required in `jsonwebtoken` features (PEM key loading gated on this feature in v10).

## 2026-05-20 — SEC-001 — sessions.rs: `sqlx::query_as` runtime violates security.md §A03 (compile-checked SQL macro required)

- File: `gateway/src/auth/sessions.rs:50`
- Symptom: `sqlx::query_as(r"SELECT ... FROM sessions WHERE id = $1")` uses the runtime variant, bypassing compile-time SQL type checking. `security.md §A03` mandates `sqlx::query_as!` macro. The existing comment "without requiring offline cache" makes the workaround explicit.
- Why blocked: `sqlx::query_as!` macro requires `cargo sqlx prepare` which needs a live PostgreSQL instance with the schema applied. This cannot run in the current dev/CI environment without a running DB. Converting without running `cargo sqlx prepare` would produce a compile error (`DATABASE_URL` not set for compile-time check, or missing `.sqlx/` cache).
- Suggested resolution:
  1. Set up a local PostgreSQL instance, apply migrations (`migrations/run.sh`), run `cargo sqlx prepare --manifest-path gateway/Cargo.toml`, commit the generated `.sqlx/` directory, then convert `sqlx::query_as` → `sqlx::query_as!` in `sessions.rs:50`.
  2. Alternatively, use `sqlx::query_as_unchecked!` as a transitional step (still macro, but no compile-time schema check) — acceptable only if annotated with a `// TODO: upgrade to query_as! once .sqlx/ cache is generated` comment.
  3. A CI job running `cargo sqlx prepare --check` against the postgres service container would prevent future regressions.
- Status: open — awaiting human to provide DB environment for `cargo sqlx prepare`

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

## 2026-05-27 — INFRA-002 — Cloud SQL `ipv4_enabled = false` impossible without private network

- File : `infra/terraform/cloud_sql.tf:23`
- Symptom : `terraform apply` fails with
  `Error 400: Invalid request: At least one of Public IP or Private IP or PSC connectivity must be enabled.`
- Why blocked : Original spec set `ipv4_enabled = false` assuming Cloud SQL Auth Proxy sidecar (Cloud Run annotation `run.googleapis.com/cloudsql-instances`) provides connectivity layer by itself. Incorrect — the SQL instance requires one of public IP, private IP (VPC peering), or PSC. The proxy routes traffic via Google backbone but the instance still needs an IP type defined.
- Suggested resolution (3 options) :
  1. **(a) Public IP + no `authorized_networks`** (chosen V1) — instance has public IP but firewall closed (deny all direct connections). Proxy connects via Google internal layer. Defense: `ssl_mode = ENCRYPTED_ONLY` + IAM-only auth (no password). Zero extra cost. Pragmatic.
  2. **(b) Private IP via VPC peering** — adds `google_compute_network` + `google_service_networking_connection` + Cloud Run requires Serverless VPC Connector (~€10/mo). Matches original spec intent (no public IP at all).
  3. **(c) Private Service Connect (PSC)** — newer pattern, lower cost than VPC connector. Requires PSC endpoint setup.
- Decision : option (a) for V1 — pragmatic, no extra cost, defense-in-depth via IAM + SSL + empty authorized_networks. Re-evaluate in V2 if compliance audit demands no-public-IP.
- Status : resolved by PR (fix/INFRA-002-cloud-sql-ipv4)
