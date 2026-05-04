# Bootstrap Checklist — Enterprise-grade LLM App

> Reusable setup for LLM apps (RAG, agents, copilots) targeting SME / regulated industries.
> Copy into a new project, tick as you go. Each item carries a one-line **why** — never forget a gate.

---

## 1. Identity & legal

- [ ] `LICENSE` — without it, default = "All Rights Reserved", blocks reuse.
- [ ] `README.md` — tagline, stack, quickstart. First impression for reviewers.
- [ ] `SECURITY.md` — disclosure path + response SLA. Required if public.
- [ ] `CHANGELOG.md` (Keep a Changelog) — auto-maintained by `release-please`.

## 2. Repository skeleton

- [ ] `.gitignore` covers `.env*`, `*.key`, `*.pem`, `*.tfstate*`, `*-sa.json`, `secrets/`, IDE, build artifacts — first line of defense before hooks fire.
- [ ] `.env.example` — onboards new devs without leaking real values.
- [ ] `docs/architecture.md` — diagram + data flow + SLOs. Shared mental model.
- [ ] `docs/runbook.md` — local dev, incidents, deployment. On-call survival.
- [ ] `docs/blockers.md` (append-only) — feeds `no-workaround` rule.
- [ ] `docs/adr/0000-template.md` + first ADR — captures *why* decisions, not *what*.

## 3. Agentic workflow (Claude Code)

- [ ] `CLAUDE.md` ≤ 150 lines — agents read this every turn, terse = cheap + accurate.
- [ ] `.claude/settings.json` **default-deny** — Bash whitelist, Read/Write scoped. Stops accidental damage.
- [ ] Sub-agents: `spec-author`, `architect`, `implementer`, `reviewer`, `eval-runner`, `debugger` — separation of concerns, narrower context, fewer hallucinations.
- [ ] Slash commands: `spec`, `plan`, `impl`, `review`, `eval`, `ship`, `ticket`, `debug` — codified workflow, no improvisation.
- [ ] Rules in `.claude/rules/`: `clean-code`, `security`, `secret-hygiene`, `vertical-slice`, `no-workaround` — shared standards across all agents.
- [ ] Hooks in `.claude/scripts/`:
  - `guard-git.sh` (PreToolUse Bash) — blocks destructive git.
  - `guard-secret-paths.sh` (PreToolUse Write) — blocks writes to secret paths.
  - `format-on-save.sh` (PostToolUse) — keeps diffs small.
  - `validate-claude-config.sh` — lints `.claude/**` + CLAUDE.md size.

## 4. Linting & formatting (strict)

Strict linters = source of truth for conventions. Pick per stack:

- [ ] Compiled langs (Rust/Go): deny `unwrap`/`panic`/`print`/`dbg`, forbid `unsafe` unless justified. Pedantic on.
- [ ] Dynamic langs (Python/TS): strict types (mypy strict / tsc strict), security ruleset (bandit `S`, eslint-security), no print, async pitfalls.
- [ ] Format check + lint run in CI — drift compounds otherwise.
- [ ] Lock files committed (`Cargo.lock`, `uv.lock`, `package-lock.json`) — reproducible builds.
- [ ] OpenAPI lint (`redocly`) + contract tests (`schemathesis`) if REST exposed — spec drift kills clients.

## 5. Pre-commit hooks

- [ ] `.pre-commit-config.yaml` + `pre-commit install` (regular + `--hook-type commit-msg`) — catches issues before CI burns minutes.
- [ ] `gitleaks` + `detect-private-key` — secret scanning at commit time.
- [ ] `check-added-large-files` (`--maxkb=500`) — keeps repo lean.
- [ ] `trailing-whitespace`, `end-of-file-fixer`, `mixed-line-ending --fix=lf` — avoids CRLF on Windows.
- [ ] `check-yaml`/`json`/`toml` — parse errors fail fast.
- [ ] Language formatters + linters wired in.
- [ ] Dep-audit hooks (`cargo-deny`, `pip-audit`, `npm audit`) — license + CVE gate.
- [ ] Conventional commits hook (commit-msg) — required for `release-please`.

## 6. CI/CD (GitHub Actions)

- [ ] Per-language workflow (fmt/lint/typecheck/test) — language gates separate.
- [ ] Contract tests job (`schemathesis`) if OpenAPI — verify deployed shape.
- [ ] Eval job (Ragas) gated on RAG path changes — quality regression gate.
- [ ] `gitleaks-action` — pre-commit ≠ CI; both needed (PRs from forks bypass local hooks).
- [ ] `dependabot.yml` (cargo + pip + npm + actions + docker) — supply chain hygiene.
- [ ] `release-please` workflow + config + manifest — semver + changelog automation.
- [ ] `PULL_REQUEST_TEMPLATE.md` (ticket ID, AC coverage, security checklist) — forces review focus.
- [ ] `ISSUE_TEMPLATE/{bug,feature}.yml` + `config.yml` (block blank issues) — triage signal.
- [ ] CI secrets configured (LLM keys, etc.) — see §13.
- [ ] Branch protection on release + dev branches — see §13.

## 7. Security baseline

- [ ] STRIDE threat model in `specs/threat-model.md` — one row per scenario, mitigation, status. Threat surface ≠ generic.
- [ ] OWASP Top 10 + LLM threats (prompt injection, embedding poisoning, output sanitization) mapped in `.claude/rules/security.md`.
- [ ] Secrets manager chosen (GCP SM / AWS SM / Vault) + ADR — no env-file-in-prod.
- [ ] Sensitive types enforced (`secrecy::Secret`, `pydantic.SecretStr`) — redacts from logs/Debug.
- [ ] `cargo-deny` + `pip-audit` configs with license allowlist — blocks viral / unknown licenses.
- [ ] `detect-secrets` baseline (`.secrets.baseline`) — prevents new leaks, accepts legacy.
- [ ] Default-deny path access on agents — sources of truth (specs, migrations, eval baseline) human-only.

## 8. Database (skip if no persistent store)

- [ ] `migrations/0001_init.sql` (extensions + `schema_version` table) — versioned from day 1.
- [ ] Migration tool documented (sqlx / alembic / atlas) — single source for schema.
- [ ] App DB user has no DDL/DROP — migrations run via separate role. Blast radius limit.
- [ ] Connection via Auth Proxy / private IP — no public DB exposure.

## 9. Observability

- [ ] Structured JSON logging (`tracing`, `structlog`, `pino`) — grep-friendly + ingestion-ready.
- [ ] OpenTelemetry SDK installed even if not exporting — door open, no refactor later.
- [ ] LLM tracing platform (Langfuse / LangSmith / Helicone) — debugging RAG without traces is blind.
- [ ] Metrics endpoint (Prometheus client) — SLO enforcement.
- [ ] **PII redaction layer before any external trace export** — LLM traces leak prompts/secrets if unfiltered.

## 10. Eval (skip if not RAG)

- [ ] `specs/golden_qa.jsonl` ≥ 20 entries before launch — human-curated reference set.
- [ ] Eval runner (Ragas) committed — faithfulness, answer relevancy, context P/R.
- [ ] `eval/baseline.json` — CI compares PR run vs baseline, blocks regression.
- [ ] CI workflow runs eval on RAG path changes only — keeps unrelated PRs fast.

## 11. Infrastructure

- [ ] `Dockerfile` per service — multi-stage, non-root, slim base. Smaller surface, smaller image.
- [ ] `docker-compose.yml` — local stack mirrors prod topology.
- [ ] IaC (Terraform/Pulumi) deferred to dedicated ticket via ADR — don't stub modules, write all at once.
- [ ] Container scanning in CI (`trivy` / `grype`) — catches base-image CVEs.

## 12. Bootstrap commands

```bash
# 1. Generate lock files — per stack (no git dependency, must exist before first commit)
# Rust:
cargo build
# Python:
uv lock
# Node:
npm install --package-lock-only

# 2. Install global CLI tools — per stack
# Rust:
cargo install cargo-deny sqlx-cli
# Python:
uv tool install pre-commit detect-secrets pip-audit
# Node:
npm i -g @redocly/cli  (if OpenAPI)

# 3. Init git (pre-commit needs .git/ to exist)
git init && git branch -m main

# 4. Activate pre-commit hooks (regular + commit-msg)
pre-commit install --install-hooks
pre-commit install --hook-type commit-msg

# 5. Generate secrets baseline
detect-secrets scan > .secrets.baseline

# 6. Sanity check
bash scripts/check-setup.sh

# 7. First commit (conventional)
git add . && git commit -m "chore: bootstrap repository"

# 8. Push
git remote add origin git@github.com:doudoune444/<repo>.git
git push -u origin main
```

## 13. GitHub repo config (one-time, post-push)

- [ ] Description + topics — discoverability.
- [ ] Default branch = `main` — trunk-based, all PRs target `main`.
- [ ] Issues enabled with templates loaded.
- [ ] **Branch protection on `main`**: PR review (≥1), required status checks (by name), linear history, block force push, block deletion.
- [ ] CI secrets: LLM keys + third-party service keys.
- [ ] Dependabot alerts + security updates enabled.
- [ ] Secret scanning + push protection enabled.
- [ ] Code scanning (CodeQL) enabled per language.
- [ ] `ISSUE_TEMPLATE/config.yml` URLs point to actual repo.

## 14. Local dev prerequisites

Pin minimum versions in repo. Examples for common stacks:

| Tool | Purpose |
|------|---------|
| Language toolchain (Rust / Python via uv / Node) | build + test |
| Docker + Compose v2 | local stack |
| pre-commit | hook orchestration |
| Migration CLI (sqlx-cli / alembic) | schema |
| `cargo-deny` / `pip-audit` / equivalent | dep audit |
| `gitleaks` | secret scan |
| `redocly` (npx) | OpenAPI lint, if REST |

## 15. Done when

- [ ] `bash scripts/check-setup.sh` exits 0.
- [ ] All test suites pass.
- [ ] `docker compose up -d` brings stack up healthy.
- [ ] `pre-commit run --all-files` exits 0.
- [ ] First conventional commit lands without hook failure.
- [ ] CI green on bootstrap PR.

## 16. Rules never to break

- **Never** `git checkout/switch/stash` — new branch + cherry-pick.
- **Never** edit human-only sources without explicit decision (acceptance specs, threat model, properties, OpenAPI, golden Q/A, eval baseline, migrations).
- **Never** disable a test to pass CI — find the cause.
- **Never** add a heavy / FFI / unsafe dep without ADR.
- **Never** commit secrets — verify `.env` gitignored before any `git add`.
- **Never** patch around a blocker silently — log in `docs/blockers.md`, stop, escalate.
- **Always** `/spec` → `/plan` → `/impl`. Workflow = contract.

## 17. Adapting to a new project

1. Replace placeholders (name, owner, license holder, contact).
2. Drop sections that don't apply (no DB → §8 ; not RAG → §10).
3. Swap stack-specific lints (Rust↔Go, Python↔TS).
4. **Recreate threat model from scratch** — surfaces differ, do not copy mitigations.
5. Re-curate golden Q/A per domain.
6. Document deviations in an ADR.

---

_Last reviewed: 2026-04-30_
