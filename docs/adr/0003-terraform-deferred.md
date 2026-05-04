# ADR 0003 — Terraform infrastructure deferred to ticket PROD-001

- Status: accepted
- Date: 2026-04-30
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

1. Three vertical slices have shipped on `develop` and a staging deployment is needed.
2. External demo / pen test requires a public endpoint.
3. First customer commitment requires production-grade infrastructure.

## References

- `docs/architecture.md` — high-level diagram and service boundaries
- `specs/threat-model.md` — STRIDE matrix referencing GCP resources
- `.claude/rules/no-workaround.md` — prohibits manual cloud console operations as workaround
