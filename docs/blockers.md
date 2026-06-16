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

## 2026-06-16 — BOARD-001 — `aws-lc-sys` NASM build failure blocks cargo clippy/test on Windows

- File : `gateway/Cargo.lock` (aws-lc-sys = 0.41.0)
- Symptom : `cargo clippy -- -D warnings` and `cargo test` fail with: `NASM command not found! Build cannot continue.` inside `aws-lc-sys v0.41.0` build script. Same failure in both the worktree and the main repo gateway (the pre-existing `gateway/target/debug/archiviste-gateway.exe` was built before aws-lc-sys 0.41.0 was introduced or when NASM was available).
- Why blocked : `aws-lc-sys >= 0.41.0` requires NASM assembler at build time on Windows x86_64-pc-windows-msvc. NASM is not installed on this machine. `AWS_LC_SYS_NO_ASM=1` skips NASM but then requires cmake, which is also missing. Pre-existing platform constraint — not introduced by BOARD-001.
- Suggested resolution : Install NASM (https://www.nasm.us/) on the Windows dev machine, or add `.cargo/config.toml` with `[target.x86_64-pc-windows-msvc] rustflags = ["--cfg", "aws_lc_sys_use_pregenerated_src"]` if that flag is supported, or pin aws-lc-sys to a version that bundles pregenerated C sources for Windows. All gateway CI runs on Linux (Cloud Run build) where this is not an issue.
- Status : open (pre-existing, not introduced by BOARD-001)

## 2026-06-02 — OPS-003 — Prod 502: migrations never applied + CLOUD_SQL_IAM_AUTH env drift

- File : `.github/workflows/deploy.yml` (no migrate step) + `infra/terraform/cloud_run.tf:180` (`CLOUD_SQL_IAM_AUTH=true`)
- Symptom : Production `POST /v1/chat` returns 502 after SEC-006 went live. Two concurrent root causes:
  1. `workers` canary booted with `CLOUD_SQL_IAM_AUTH` missing from live env (Cloud Run env drift vs Terraform declaration at `cloud_run.tf:180`) — asyncpg `password=None` boot crash; crashed revision had `--no-traffic` so it was never smoke-probed; gateway smoke tested the live workers (not the canary), passed, promote completed with a broken workers revision.
  2. Prod Cloud SQL had zero applied migrations (`schema_version` table absent, no `chunks`/`documents` tables) — `deploy.yml` had no migration step; `migrations/run.sh` only ran in local CI against the docker-compose postgres.
- Why blocked : The combination of canary deploy with `--no-traffic` (Cloud Run startup probe runs but smoke goes to live traffic path) and missing DB probe (`/readyz`) meant a DB-failing canary could be promoted silently. `deploy.yml` lacked a migration step entirely — no ticket had ever wired `run.sh` against prod Cloud SQL.
- Manual hotfix applied 2026-06-02 :
  1. `gcloud run services update archiviste-workers --update-env-vars CLOUD_SQL_IAM_AUTH=true` to fix the env drift on the live revision.
  2. `gcloud run services update-traffic archiviste-workers --to-revisions=LATEST=100` to restore traffic to a working revision.
  3. Ran cloud-sql-proxy + `migrations/run.sh` manually against prod Cloud SQL as `archiviste-runtime` SA to apply all pending migrations.
  4. Connected as `postgres` (cloudsqlsuperuser), ran `GRANT USAGE, CREATE ON SCHEMA public TO "archiviste-runtime@flamme-496014.iam"` and `CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE EXTENSION IF NOT EXISTS vector;`.
- Suggested resolution : OPS-003 — (a) add `/readyz` DB probe to workers + Cloud Run `startup_probe`; (b) add migrate step in `deploy.yml` after canary deploy, before promote; (c) extend rollback `if:` to cover migrate/canary_ready failures; (d) grant `gha-deploy` `tokenCreator` on runtime SA so CI can impersonate it for migrations.
- Status : resolved by OPS-003 (this branch)

## 2026-05-29 — SEC-004 — `sec004_network_error_logs_network` asserts `network` but Windows returns `timeout`

- File : `gateway/tests/test_signed_url.rs:676`
- Symptom : `assert_eq!(err.reason_code(), "network")` fails on Windows dev machine with `left: "timeout"`. The test uses an ephemeral port bound then dropped to force ECONNREFUSED. On Linux, the OS sends RST immediately → reqwest classifies as `network`. On Windows, the TCP stack does not send RST for loopback; the connection attempt waits for the connect timeout (2 s) → reqwest classifies as `timeout`.
- Why blocked : Platform difference. On Linux CI (`ubuntu-latest`) the test passes. On Windows dev the test fails. Changing the assertion to `|| "timeout"` was the pre-review behavior — the review explicitly tightened it to strict `"network"` for spec compliance.
- Suggested resolution : Accept as Windows-only local failure. CI (Linux) is the authoritative test environment for the production platform. No production code change needed. Document with `#[cfg_attr(target_os = "windows", ignore)]` if Windows dev friction becomes a concern — human decision needed.
- Status : open — CI (Linux) passes; Windows local dev fails

## 2026-05-29 — INFRA-002 — PR-f: gateway empty password on Cloud SQL IAM (Cloud Run integrated proxy does not inject token)

- File : `infra/terraform/cloud_run.tf:73` + `gateway/src/lib.rs:495`
- Symptom : after PR-f merge (DATABASE_URL `postgres://<sa>@localhost/archiviste?host=/cloudsql/...`), gateway boot crashes on sqlx pool connect. sqlx returns the empty password supplied in the URL (no password component between `:` and `@`) verbatim to Cloud SQL, which rejects the IAM auth handshake (`password authentication failed for user "archiviste-runtime@flamme-496014.iam"`). Cloud SQL instance has `cloudsql.iam_authentication=on` (PR #77) and SA is granted `roles/cloudsql.client` + `roles/cloudsql.instanceUser` (PR-e). The PG password slot must contain a short-lived IAM OAuth access token (1h TTL) fetched from the metadata server — Cloud Run v2's integrated proxy provides the Unix socket but does NOT transparently inject this token client-side, contrary to the initial design assumption in runbook §8.
- Why blocked : `--auto-iam-authn` flag on a sidecar `cloud-sql-proxy` is the standard token-injection path, but Cloud Run v2 integrated proxy (`run.googleapis.com/cloudsql-instances` annotation) does not expose this flag. Tried documenting the empty-password path in PR-f assuming proxy-side injection; verification deploy proves the assumption wrong. No client-side token injection exists today in `lib.rs:495` (`PgPoolOptions::new().connect(&config.database_url)` is a one-shot connect with the URL as-is). Workers (`asyncpg`) likely has the same latent bug — unverified, prod workers status unknown.
- Suggested resolution : two-step. (1) Ship SEC-004 — gateway GCS signing via metadata-server OAuth (`TokenProvider` at `gateway/src/gcs/token.rs`, cached 5-min, mockable). (2) Ship SEC-005 (new ticket) — reuse `TokenProvider` (renamed to `gateway/src/auth_metadata/token.rs` as part of SEC-005) with `sqlx::PgPoolOptions::before_acquire` hook injecting a fresh IAM token (scope `https://www.googleapis.com/auth/sqlservice.admin`) per connection acquisition. Workers symmetry decided at SEC-005 plan time (gateway-only vs gateway+workers single PR ≤300 LOC). Fallback Option A (PG password in Secret Manager) explicitly rejected by operator.
- Status : open — SEC-004 spec ready, SEC-005 to be authored after SEC-004 ships

## 2026-05-27 — INFRA-002 — Cloud SQL IAM authentication rejected: `cloudsql.iam_authentication` flag missing on instance

- File : `infra/terraform/cloud_sql.tf:8` (resource `google_sql_database_instance.archiviste_db` `settings {}`)
- Symptom : `psql` connection via `cloud-sql-proxy --auto-iam-authn` fails for both CLOUD_IAM_SERVICE_ACCOUNT (`archiviste-runtime@flamme-496014.iam`) and CLOUD_IAM_USER (`<operator-email>`) with `FATAL: Cloud SQL IAM <service account|user> authentication failed for user "..."`. Proxy reaches the instance (no network error, no 403 from impersonation after token-creator grant), Cloud SQL itself rejects the token at auth time. `gcloud sql instances describe archiviste-db --format="value(settings.databaseFlags)"` returns empty.
- Why blocked : Cloud SQL requires the instance-level database flag `cloudsql.iam_authentication=on` to accept any IAM auth token. `cloud_sql.tf` declares the SA IAM user (`google_sql_user.archiviste_runtime` type `CLOUD_IAM_SERVICE_ACCOUNT`) and the runtime SA has `roles/cloudsql.client` + `roles/cloudsql.instanceUser`, but the flag itself was never added to `settings {}`. Without it, IAM auth is structurally impossible regardless of user/role setup. Bootstrap runbook §8 verification cannot pass → `deploy.yml` first run would crash on Cloud SQL connect from gateway/workers.
- Suggested resolution : add `database_flags { name = "cloudsql.iam_authentication" value = "on" }` inside `settings {}` of `google_sql_database_instance.archiviste_db`. Per GCP docs the flag is dynamic (no restart, no data loss). Apply via fix PR `fix/INFRA-002-sql-iam-auth-flag` + `terraform apply`. Re-run runbook §8 verification after apply.
- Status : open

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
- Status : resolved by ING-016 — `chunker.py` rewritten to load vendored Mixtral-8x7B-v0.1 `tokenizer.json` via `tokenizers.Tokenizer.from_file`; `transformers` + `sentence-transformers` removed from all dep tables (AC-1/AC-5/AC-6).

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

## 2026-05-27 — INFRA-002 — Cloud Run placeholder image `pause:3.9` cannot pass startup probe

- File : `docs/runbook/bootstrap-gcp.md` step 4b
- Symptom : `terraform apply` of `google_cloud_run_v2_service.workers` fails with
  `The user-provided container failed to start and listen on the port defined provided by the PORT=8080 environment variable within the allocated timeout.`
- Why blocked : Original spec instructed pushing `gcr.io/google-containers/pause:3.9` as a placeholder to satisfy Cloud Run's image-must-exist requirement. `pause` is a no-op infinite-sleep container that never listens on any port — Cloud Run startup probe fails systematically.
- Suggested resolution : replace placeholder image with Google's official Cloud Run hello sample (`us-docker.pkg.dev/cloudrun/container/hello`) which is designed exactly for this case — listens on `$PORT` env var, returns 200 on `/`. Also requires forcing a new Cloud Run revision via `gcloud run services update --image=...` because Cloud Run pins by image digest at first deploy; subsequent pushes to the same `:latest` tag do not auto-redeploy without an explicit trigger.
- Status : resolved by PR (fix/INFRA-002-runbook-cloud-run-placeholder)

## 2026-05-27 — INFRA-002 — Cloud Run domain mapping unavailable in europe-west9

- File : `infra/terraform/cloudflare.tf:38` (resource `google_cloud_run_domain_mapping.archiviste_fr`)
- Symptom : `terraform apply` fails with
  `Error 501: Creating domain mappings is not allowed in europe-west9.`
- Why blocked : `google_cloud_run_domain_mapping` is available only in a limited set of regions (us-central1, us-east1/4, us-west1, europe-west1, asia-east1, asia-northeast1). europe-west9 (Paris) is not in the list. The original spec assumed mapping availability without checking the regional matrix.
- Decision (option a, chosen) : drop the domain mapping resource. Cloudflare already serves as the reverse proxy (CF orange-cloud proxied=true), so the GCP-side TLS termination via `ghs.googlehosted.com` is redundant. New flow:
  - DNS: CNAME `archiviste.nocilia.fr` → \<gateway\>.run.app (Cloud Run actual hostname via `gateway.uri`)
  - CF terminates TLS at edge with its own cert
  - CF → Cloud Run over HTTPS, SNI = *.run.app (Google cert), Full Strict satisfied
  - Cloud Run service uses INGRESS_TRAFFIC_ALL, accepts client Host header forwarded by CF
- Alternatives rejected :
  - (b) Migrate region to europe-west1 — supports mappings, but requires destroying Cloud SQL (data loss risk in future even if V1 empty) and full state churn.
  - (c) Global LB + Serverless NEG — +€18/mo, more Terraform surface area.
- Status : resolved by PR (fix/INFRA-002-drop-domain-mapping)

## 2026-06-01 — SEC-001 / SEC-006 post-deploy — workers reject all `/v1/generate` calls with 400 `invalid_user_id`

- File : `workers/src/archiviste_workers/generate/models.py:28-29` (Pydantic `GenerateRequest` requires `user_id` + `user_tier` in body) vs `gateway/src/handlers/chat.rs:184-188` (body omits both — comment cites SEC-001 "headers are canonical transport").
- Symptom : Production `POST /v1/chat` returns `502 upstream_error` after ~27s. Cloud Run httpRequest log shows workers responded `400` on `/v1/generate`. Gateway logs show no `chat.id_token_failed` (ID-token signing works). Authentication via SEC-006 IAM + Bearer works end-to-end. Workers reach `_parse_request`, Pydantic `model_validate` raises `ValidationError` on missing `user_id`, mapped to `_GenerateError(400, "invalid_user_id")`.
- Why blocked : SEC-001 AC-14 shipped gateway-side identity propagation via `X-User-Tier` + `X-User-Id` headers and removed identity from the JSON body, but no complementary workers-side change ever consumed those headers. `grep -r x-user-id workers/` returns 0 hits. The bug was dormant while workers ingress was `INGRESS_TRAFFIC_INTERNAL_ONLY` (no real traffic could reach workers from gateway end-to-end pre-SEC-006). SEC-006 flipped ingress to ALL + added ID-token signing, exposing the broken contract on first live call.
- Resolution : PR #93 (`fix(workers): FIX-SEC-001 consume X-User-Id/X-User-Tier headers in generate route`) — workers `/v1/generate` reads identity from headers, server-side validation added.
- Status : resolved by PR #93.

## 2026-06-03 — INFRA (CF Host override) — Cloudflare Free plan blocks Origin Rule Host Header Override

- File : `infra/terraform/cloudflare.tf` (resource `cloudflare_ruleset.archiviste_fr_origin_host`, phase `http_request_origin`, added by PR #107).
- Symptom : `archiviste.nocilia.fr` returns a Google 404 (`*.run.app` works). PR #107 added an Origin Rule to rewrite the origin Host, but `terraform apply` failed in two stages:
  1. `request is not authorized` — CF API token lacked the Origin Rules permission.
  2. After adding **Zone → Origin → Edit**: `not entitled to use the HostHeader override`.
- Why blocked : Cloud Run's frontend routes by Host header and only recognizes the `*.run.app` hostname; a forwarded visitor Host of `archiviste.nocilia.fr` 404s before reaching the gateway (`INGRESS_TRAFFIC_ALL` is irrelevant to this routing layer). The Origin Rule "Host Header Override" that fixes this is a **paid-plan Cloudflare feature** — the zone is on Free, so it cannot be enabled via token or Terraform. The original INFRA-002 assumption ("Cloud Run accepts client Host header forwarded by CF", this file 2026-05-27) was wrong, and the Origin Rule remedy is paywalled.
- Resolution : replaced the Origin Rule with a **Cloudflare Worker reverse-proxy** (Free-plan capable). `cloudflare_workers_script.host_proxy` (`infra/terraform/workers/host-proxy.js`) + `cloudflare_workers_route` on `archiviste.nocilia.fr/*` rebuild each request URL with the `<gateway>.run.app` hostname, so the outbound `fetch` derives the correct Host/SNI and Cloud Run routes to the gateway. Token gained **Account > Workers Scripts:Edit** + **Zone > Workers Routes:Edit** (see `variables.tf`).
- Status : resolved by PR (fix/cf-worker-host-proxy).

## 2026-06-03 — OPS-005 — Cloud Run Job cannot access `lore/` markdown (not in workers image)

- File : `infra/terraform/cloud_run_job.tf:44` ; `infra/docker/workers.Dockerfile:7-9` ; `.github/workflows/deploy.yml` (build workers) ; `workers/src/archiviste_workers/ingest/cli.py:58-73`.
- Symptom : workflow `ingest-lore` run 26898843402 → execution `archiviste-ingest-hspqs` `Failed` in ~3s. Cloud Logging: `ModuleNotFoundError: No module named 'archiviste_workers'` then `Container called exit(1)`. Workflow failure propagation itself worked correctly (AC-5/AC-7 verified: gcloud `--wait` → red run).
- Why blocked : three compounding defects. (A) Job ran bare `python -m archiviste_workers.ingest`; the package lives in a uv venv (`/app/.venv`), the workers service activates it via `uv run` — bare `python` is the system interpreter → ModuleNotFoundError. (B) The workers image does NOT contain `lore/` (Dockerfile copies only `pyproject.toml`+`src/`, build `context: workers`; `lore/` is at repo root). The ingest CLI further *requires* a `.git/` checkout: `find_repo_root` walks up for `.git/`, `resolve_target` enforces `--path` inside repo root, and `source_path` is stored relative to repo root. So a mounted bucket or baked dir would also fail. (C) `deploy.yml` pushed only `workers:${sha}`, never `:latest`, but the Job pins `workers:latest` → the Job never received pipeline images.
- Suggested resolution : an earlier draft of this branch resolved (B) by having the Job `git clone` the public repo at runtime. **Rejected** — the public clone only contains `lore/sample/*.md` (2 fixtures); the real corpus is intentionally kept out of the public repo (`.gitignore` `/lore/*`, spoilers — repo going public). A clone would silently ingest only the samples and never the real lore. The corpus lives on Google Drive (source of truth, ING-013) + the author's local disk only; it is NOT in git and NOT in any GCS bucket yet. Defects (A)(C)(D) are independent of the corpus channel and are fixed in this branch; (B) is split into **OPS-006** (private corpus channel).
- Resolution : (A) Job runs `/app/.venv/bin/python` (uv venv interpreter). (C) `deploy.yml` now also tags `workers:latest`. (D) Adversarial review found a fourth defect: `_run_async` in `workers/src/archiviste_workers/ingest/cli.py` called `create_pool(settings.database_url)` with NO `token_provider` argument, so the Job would always attempt password auth and fail DB auth with `CLOUD_SQL_IAM_AUTH=true` (exit 2). Fixed by mirroring the `main.py` lifespan pattern: `SqlTokenProvider() if settings.cloud_sql_iam_auth else None` built before pool creation, passed as `token_provider=`, and `aclose()`d in the `finally` block. `TokenFetchError` added to the init `except` tuple so token-fetch failure maps to `EXIT_INIT_FAILURE` (exit 2), consistent with ING-001 AC-17. Test coverage added in `workers/tests/test_ingest_cli.py`. (B) DEFERRED to OPS-006: a `git init` ephemeral root + `gcloud storage rsync gs://archiviste-lore-corpus lore/` (corpus published Drive → private GCS bucket by an extended gdrive-sync), re-triggered via `workflow_run` after gdrive-sync (the `push paths: lore/**` trigger can never fire while `/lore/*` is gitignored).
- Status : (A)(C)(D) RESOLVED in code (branch `fix/OPS-005-job-clone-repo`, pending merge + redeploy). Job remains non-functional (exit 2, no `.git/`) until (B) lands in OPS-006.
