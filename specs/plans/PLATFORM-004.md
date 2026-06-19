# Plan — PLATFORM-004 second Cloud Run service + strict CSP headers

## Goal
Deploy `frontend/` (Next.js) as a second Cloud Run service (`min-instances=0`) that becomes the sole public web origin, with the gateway demoted to non-browser-reachable, and emit CSP/nosniff/Referrer-Policy at least as strict as the gateway's.

## Acceptance criteria recap
NOTE: `specs/acceptance/PLATFORM-004.md` does NOT exist (verified). AC below transcribed from gh issue #193 — confirm a spec file is authored before implementation.
- Terraform defines a second Cloud Run service for the frontend with min-instances=0
- The deployed frontend serves the app over a single public origin; the gateway is not browser-reachable
- The frontend emits a CSP at least as strict as the gateway's, plus nosniff + Referrer-Policy
- Idle cost stays near zero (service scales to zero)

## Files to touch
- `infra/docker/frontend.Dockerfile` — new; multi-stage Node build, Next.js `standalone` output
- `frontend/next.config.ts` — add `output: "standalone"`; tighten CSP to match gateway (see Risks)
- `infra/terraform/cloud_run.tf` — new `google_cloud_run_v2_service.frontend`; flip `gateway_public_invoker` → frontend-SA-only; add `frontend_public_invoker` (allUsers); gateway `ingress = INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` decision (see topology)
- `infra/terraform/checks.tf` — add `gateway_iam_no_public_invoker` check (parallel to workers)
- `infra/terraform/cloudflare.tf` — repoint CNAME content + Worker `ORIGIN_HOST` from gateway.uri → frontend.uri
- `infra/terraform/outputs.tf` — add `frontend_url` output
- `.github/workflows/deploy.yml` — add build+push frontend image, canary+promote frontend service
- `frontend/Dockerfile`? NO — keep image build under `infra/docker/` per existing convention
- `docs/architecture.md` — update topology diagram (browser → frontend → gateway)
- `CHANGELOG.md` — `## [Unreleased]` entry

## Topology decision (IAM + ingress)
- `frontend`: `ingress = INGRESS_TRAFFIC_ALL` + `google_cloud_run_v2_service_iam_member.frontend_public_invoker` member `allUsers`. New public origin.
- `gateway`: keep `ingress = INGRESS_TRAFFIC_ALL` (Cloudflare-fronted today; flipping to internal-LB needs a connector/LB it doesn't have — DO NOT change ingress). Replace `allUsers` invoker with member `serviceAccount:${archiviste_runtime.email}`. IAM is the trust boundary (mirrors workers SEC-006 pattern).
- Cloudflare: CNAME + Worker `ORIGIN_HOST` move to `frontend.uri`. Gateway run.app stays IAM-gated; no public DNS points at it.
- New `checks.tf` block asserts gateway invoker member ∉ {allUsers, allAuthenticatedUsers}.

## RESOLVED DECISIONS (human, 2026-06-18)
- **Single PR.** The slices are file-coupled (cloud_run.tf, next.config.ts) and B depends on A's frontend SA — not safely parallelizable. Build the whole vertical slice in one PR, in dependency order. The 300-LOC cap is explicitly waived for this ticket.
- **AC-2 = IAM gating (in scope).** Gateway invoker flips `allUsers → archiviste_runtime` SA. `bff-proxy.ts` attaches a metadata-server ID token (`Authorization: Bearer <id_token>`, audience = gateway URL) via `/computeMetadata/v1/instance/service-accounts/default/identity?audience=<gateway_url>`, mirroring `gateway/src/auth_metadata/id_token.rs`. DNS-only is rejected — it leaves the gateway run.app URL world-invokable, failing AC-2. The bff-proxy change touches a human-reviewed deep module: implementer keeps it minimal; the human reads this diff line-by-line and the reviewer is run hard on it.
- **AC-3 = nonce CSP (in scope).** Add Next.js middleware emitting a per-request nonce; CSP reaches gateway parity (`script-src 'self' 'nonce-…'; style-src 'self' 'nonce-…'; base-uri 'none'; form-action 'self'; img-src 'self' data:`). `'unsafe-inline'` is rejected (weaker than gateway, fails "at least as strict"). Accept that nonce forces dynamic rendering (min-instances=0 already accepts cold/dynamic). MUST verify hydration with Playwright before commit — if a lib injects un-nonced inline style, surface it (no-workaround).

## Frontend image build
No frontend Dockerfile exists (verified `infra/docker/` + `frontend/`). Build pattern mirrors `infra/docker/gateway.Dockerfile`: multi-stage, `context: frontend`, `file: infra/docker/frontend.Dockerfile`, tags `:<sha>` (+ `:latest` only if a Cloud Run Job pins it — none does, so `:sha` like gateway). Requires `output: "standalone"` in next.config.ts so the runtime stage copies `.next/standalone` + `.next/static` + `public`. Cloud Run port: Next.js `next start` listens on `$PORT` (Cloud Run sets 8080) — no `ports{}` override needed (unlike workers:8000).

## CSP verification approach
- Gateway actual CSP (`gateway/src/lib.rs:480`) is STRICTER than the issue text claims: `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'`.
- Frontend current CSP (`next.config.ts`) omits `script-src/style-src/base-uri/form-action` → NOT "at least as strict". Tighten to match gateway. Risk: Next.js hydration may need inline scripts → may force nonce work (the file's deferred-nonce comment). Confirm App Router pages render under strict `script-src 'self'` before committing; if inline blocked, this becomes a nonce-middleware sub-task (scope risk — flag, do not silently add).
- Verify via Playwright smoke (`frontend/tests/e2e/`): assert response headers contain the three headers with exact strict values.

## Test / verification strategy
- `terraform validate` + `terraform plan` (no apply in CI gate) for the new service + IAM + checks block.
- New `checks.tf` assert is the gateway public-invoker guard (verified at plan/apply).
- Frontend unit (Vitest): no new logic unless bff-proxy ID-token added (then mock metadata fetch).
- Frontend e2e (Playwright): header assertions for CSP/nosniff/Referrer-Policy.
- Post-deploy manual: curl gateway run.app URL → expect 403 (browser-unreachable); curl frontend origin → 200.
- No OpenAPI change (contract untouched). No migration. No Ragas.

## Implementation steps (ordered — single PR)
A. **Ship frontend image + service**
   1. `frontend/next.config.ts`: add `output: "standalone"`.
   2. `infra/docker/frontend.Dockerfile`: multi-stage Node build → standalone runtime (`$PORT`=8080, no `ports{}` override).
   3. `infra/terraform/cloud_run.tf`: add `google_cloud_run_v2_service.frontend` (min=0, max=20 guard, runtime SA, `GATEWAY_URL` env = gateway.uri, image `:latest` placeholder + lifecycle ignore image); add `frontend_public_invoker` (allUsers).
   4. `infra/terraform/cloudflare.tf` + `outputs.tf`: repoint origin `ORIGIN_HOST`/CNAME → frontend.uri; add `frontend_url` output.
   5. `.github/workflows/deploy.yml`: frontend build/push (`context: frontend`, `file: infra/docker/frontend.Dockerfile`, `:sha`) + canary/promote frontend service.
B. **Lock the gateway** (security — read this diff line-by-line)
   6. `infra/terraform/cloud_run.tf`: flip `gateway_public_invoker` member `allUsers` → `serviceAccount:${archiviste_runtime.email}`.
   7. `infra/terraform/checks.tf`: add `gateway_iam_no_public_invoker` check, parallel to workers.
   8. `frontend/src/lib/bff-proxy.ts`: attach metadata ID token (audience = gateway URL) on the outbound gateway fetch. Unit-test with mocked metadata fetch.
C. **CSP parity**
   9. `frontend/middleware.ts` (new): per-request nonce → request/response headers; CSP to gateway parity. Move/derive CSP from `next.config.ts` headers() as needed.
   10. Playwright e2e: assert the three security headers + verify pages hydrate (no CSP console violations).
D. **Docs**
   11. `docs/architecture.md` topology (browser → frontend → gateway) + `CHANGELOG.md` `## [Unreleased]`.

## Risks / open questions
- AC-2 wording ("not browser-reachable") = IAM-gated or DNS-only? Drives the BLOCKER + slice size.
- Gateway CSP is stricter than issue text; matching it may require Next.js nonce middleware (deferred per next.config comment) → could exceed 300 LOC / need its own ticket.
- bff-proxy is a humain-reviewed deep module (PLATFORM-002 invariants); ID-token injection there needs human sign-off.
- `cloudflare.tf` repoint to a scale-to-zero frontend means cold-start latency on the public origin (accepted per ADR-0012).
- `min-instances=0` on frontend confirmed = near-zero idle cost (AC-4); Cloudflare/gateway unchanged on cost.
- No `specs/acceptance/PLATFORM-004.md` — author/confirm before coding.

## Estimated LOC
Terraform + Dockerfile + workflow + next.config + smoke ≈ 180–230 LOC. WITH bff-proxy ID-token + nonce middleware ≈ 350+ → split required. Keep ID-token/nonce out unless human rules them in-scope.

## Out of scope
- Nonce-based / hash-based CSP hardening (unless strict script-src proves to block hydration — then separate ticket).
- bff-proxy POST/PUT/DELETE body forwarding.
- App-level rate limiting (V2 SEC-002), Cloudflare ruleset rate-limit.
- Gateway ingress change to internal-LB / VPC connector.
- Any new view/page; any gateway handler change.
