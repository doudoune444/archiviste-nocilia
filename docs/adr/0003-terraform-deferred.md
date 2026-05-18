# ADR 0003 — Terraform infrastructure (deferred → activated under INFRA-002)

- Status: accepted (amended 2026-05-18 — activation triggered)
- Date: 2026-04-30 (initial), 2026-05-18 (activation)
- Decider: Doudoune

## Context

The bootstrap phase (FOUND-001) intentionally ships only :

- `infra/docker/gateway.Dockerfile`
- `infra/docker/workers.Dockerfile`
- `docker-compose.yml` (local dev stack)

Production targets (Cloud Run + Cloud SQL + GCS + Secret Manager + IAM + Cloud Logging + Workload Identity Federation) are documented in `docs/architecture.md` and `specs/threat-model.md`, but no Terraform modules exist yet.

`infra/terraform/` is currently a placeholder directory.

## Decision

Defer all Terraform implementation to a dedicated ticket : **PROD-001 — provision GCP infrastructure**.

`PROD-001` will :

1. Bootstrap Terraform state backend (GCS bucket + state locking).
2. Provision Cloud Run services (gateway, workers) with `ingress=internal` for workers.
3. Provision Cloud SQL instance (Postgres 16 + pgvector extension) with private IP only, Cloud SQL Auth Proxy.
4. Provision GCS bucket `archiviste-conversations` with uniform bucket-level access + public access prevention enforced + object versioning + lifecycle rules.
5. Provision Secret Manager secrets (LLM API keys, DB password, Langfuse keys, OAuth Google client secret) with rotation policies.
6. Provision IAM service accounts with least-privilege bindings + Workload Identity Federation (no static SA JSON keys).
7. Provision Cloudflare zone + DNS + WAF rules + bot management.
8. Provision Cloud Logging + Cloud Monitoring (SLO dashboards, alert policies).
9. Document `terraform plan` + `terraform apply` workflow in `docs/runbook.md`.
10. Add CI workflow `.github/workflows/terraform.yml` (fmt + validate + plan on PR, no auto-apply).

## Rationale

Why defer instead of stubbing now :

- **YAGNI** — pre-code phase, no service to deploy yet.
- **Avoid premature abstraction** — Terraform module shape depends on actual service requirements that emerge during development.
- **Single coherent slice** — provisioning all GCP resources together (≈800–1200 LOC HCL) is its own vertical slice. Stubbing partial modules now creates drift and dead code.
- **Security review needs full context** — IAM bindings, network topology, and KMS decisions benefit from being reviewed as a whole, not piecemeal.

## Consequences

Positive :

- Bootstrap stays minimal and reviewable.
- Terraform state backend created intentionally, not by accident.
- IaC review is concentrated in one well-scoped PR with full security context.

Negative :

- Production deployment blocked until `PROD-001` ships.
- Manual GCP console operations forbidden in the meantime — no shadow infrastructure.
- Local docker-compose remains the only runtime path until `PROD-001`.

## Trigger conditions for PROD-001

Open `PROD-001` when **any** of the following becomes true :

1. Three vertical slices have shipped on `main` and a staging deployment is needed.
2. External demo / pen test requires a public endpoint.
3. First customer commitment requires production-grade infrastructure.

## Activation 2026-05-18

Trigger #2 met : V1 beta vitrine grande entreprise (`docs/vision.md` § Cible deploy V1 beta) requires a public endpoint on `https://archiviste.nocilia.fr`.

Ticket renamed `PROD-001` → **`INFRA-002`** to match repo nomenclature (cf. `specs/README.md`). Scope reduced for V1 fast (V2 follow-up ~1 week later) :

**In V1 INFRA-002 scope** (Q1-Q20 décisions, cf. vision.md) :

1. Terraform state backend (GCS bucket + state locking).
2. Cloud Run services (gateway 256 MB, workers 512 MB, scale-to-zero), region `europe-west9`.
3. Cloud SQL `db-f1-micro` Postgres 16 + pgvector, Auth Proxy sidecar Unix socket (no VPC connector V1).
4. GCS bucket `archiviste-conversations` uniform bucket-level access + lifecycle TTL 30j.
5. Secret Manager : 1 shared `MISTRAL_API_KEY` (LLM + embed).
6. IAM : 1 SA deploy `gha-deploy@` + 1 SA runtime partagé `archiviste-runtime@`. Workload Identity Federation, OIDC trust `repo == doudoune444/archiviste-nocilia && ref == refs/heads/main`.
7. Cloudflare zone + DNS `archiviste.nocilia.fr` + Page Rules 301 redirects `.com`/`.org`/`.eu`/`.net` → `.fr`. TLS Full Strict, Bot Fight Mode ON, 1 rate-limit rule 100 req/min/IP.
8. Budget alert €50/mois → email (Cloud Billing).
9. GHA `.github/workflows/deploy.yml` : build → Artifact Registry → canary 0 % → smoke test → promote 100 % ou auto-rollback.
10. Runbook `docs/runbook/rollback.md` (3 cmds gcloud + PITR Cloud SQL safety net).

**Deferred to V2** (cf. vision.md ordre V2) :

- VPC connector + Memorystore Redis (SEC-002 app-level rate-limit + cache).
- Cost-guard app-level + fallback chain (SEC-010).
- Full observability : uptime checks + log-based metrics + alert policies (OBS-001).
- Workers runtime SA split from gateway runtime SA (least-privilege strict, lié à SEC-001 auth tiers).

LOC estimate revised : ≈600-900 HCL (V1 scope réduit vs. ≈800-1200 initial estimate).

## References

- `docs/architecture.md` — high-level diagram and service boundaries
- `docs/vision.md` § Cible deploy V1 beta + Décisions V1 beta (Q1-Q20)
- `specs/threat-model.md` — STRIDE matrix referencing GCP resources
- `.claude/rules/no-workaround.md` — prohibits manual cloud console operations as workaround
