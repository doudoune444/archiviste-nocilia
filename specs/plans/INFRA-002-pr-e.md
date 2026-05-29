# Plan — INFRA-002 PR-e Gateway boot env vars (JWT secrets + GCS signing placeholders)

## Revision history

- **2026-05-28 (v2)** — human-approved revisions:
  - **Reco 1 (GCS placeholder)**: drop throwaway PKCS#8 PEM literal; replace with plain sentinel string `"placeholder-removed-by-SEC-004"` inline. Verified safe: `Config::from_env` (gateway/src/config.rs:81-84) does NO PEM parsing at boot — just `SecretString::from(env::var(...))`. `sign_get` (gateway/src/gcs/sign.rs:64-67) is the ONLY parse site and is handler-only (V1 has no reachable pre-auth route hitting it; auth tier MVP hardcodes `anonymous`). SEC-004 drops the field entirely, so future-needs vanish with the refactor. gitleaks:allow annotation no longer needed.
  - **Reco 2 (JWT public key via Secret Manager)**: mirror the private-key pattern. Verified consistent: both JWT keys are lazy-parsed per-request (`DecodingKey::from_ed_pem` at jwt.rs:75, `EncodingKey::from_ed_pem` at jwt.rs:110), so Secret Manager substitution at boot is operationally symmetric. Eliminates the operator-paste step + Terraform reapply on rotation, removing the only blocking risk from v1's Risks section. Cost: +1 `google_secret_manager_secret` (~$0.06/mo) + 1 IAM binding ≈ 10 LOC HCL.

## Pre-flight

(a) Files/dirs read in the worktree:
- `CLAUDE.md`, `.claude/rules/{clean-code,no-workaround,secret-hygiene,security,vertical-slice}.md`
- `specs/acceptance/INFRA-002.md`
- `infra/terraform/secrets.tf`, `infra/terraform/cloud_run.tf`, `infra/terraform/iam.tf`
- `gateway/src/config.rs` (full file — confirmed no PEM parsing at boot)
- `gateway/src/auth/jwt.rs` lines 60-120 (confirmed lazy per-request parse for both keys)
- `gateway/src/gcs/sign.rs` (confirmed `parse_rsa_key` only invoked inside `sign_get` handler path)
- `docs/runbook/bootstrap-gcp.md`

(b) Key hypotheses (corrected from v1):
1. The `archiviste-runtime` SA already has `roles/secretmanager.secretAccessor` (iam.tf L43) — per-secret IAM bindings are defense-in-depth, not strictly required.
2. **`Config::from_env` performs ZERO cryptographic validation**. All 4 PEM-shaped env vars are loaded via raw `std::env::var(...)` → `SecretString::from(String)` (config.rs:63-84). JWT keys parse lazily in `jwt::verify`/`jwt::sign` (per-request). GCS key parses only inside `sign_get` (handler-only, unreachable in V1 because no pre-auth route invokes the signer and auth tier MVP is hardcoded `anonymous` per INFRA-002 non-goals). → A plain sentinel string satisfies boot for GCS_SIGNING_PRIVATE_KEY_PEM without any crash risk.
3. Following the existing `mistral_api_key` pattern (secrets.tf L3-10, cloud_run.tf L143-151) is the conventional in-repo approach — `replication { auto {} }`, version bootstrapped post-apply by operator via `gcloud secrets versions add`, never committed.

(c) Zones of uncertainty:
- **`local.labels` existence** — used by `mistral_api_key` (secrets.tf L5); implementer must `grep -r "labels\s*=" infra/terraform/` to confirm the locals source before reusing. If absent, drop the `labels` line from the 2 new secret resources.
- **Sentinel-string compatibility with `Config::from_env`** — `std::env::var` returns the literal string verbatim; `SecretString::from(String)` is a plain `From` impl (no validation). Re-confirmed: no parse, no length check, no charset check. Zero added risk.

## Goal

Provision the four env vars the gateway requires at boot (per `gateway/src/config.rs:58-87`) so the `archiviste-gateway` Cloud Run revision starts and `/healthz` returns 200 — unblocking INFRA-002 AC-14. Terraform-only change; no gateway code edits; SEC-004 will properly handle GCS signing later.

## Acceptance criteria recap

INFRA-002 PR-e is an **amendment**, not a new ticket. It unblocks the existing AC-14:

> AC-14 : Post-merge et exécution réussie du workflow `deploy.yml` sur `main`, `https://archiviste.nocilia.fr/healthz` répond HTTP 200 depuis l'extérieur (Cloudflare + Cloud Run gateway en place, TLS valide, redirect `.com`/`.org`/`.eu`/`.net` → `.fr` actifs).

No new AC are introduced. No `specs/acceptance/INFRA-002.md` edit.

## Files to touch

- `infra/terraform/secrets.tf` — append 4 resources:
  - `google_secret_manager_secret.jwt_ed25519_private_key`
  - `google_secret_manager_secret_iam_member.jwt_ed25519_private_key_accessor`
  - `google_secret_manager_secret.jwt_ed25519_public_key` (mirror of private)
  - `google_secret_manager_secret_iam_member.jwt_ed25519_public_key_accessor`
- `infra/terraform/cloud_run.tf` — inside `google_cloud_run_v2_service.gateway` → `template.containers` (after L79 `volume_mounts`), add 4 `env` blocks:
  - `JWT_ED25519_PUBLIC_KEY_PEM` — `value_source.secret_key_ref`, version `latest`.
  - `JWT_ED25519_PRIVATE_KEY_PEM` — `value_source.secret_key_ref`, version `latest`.
  - `GCS_SIGNING_SA_EMAIL` — plain env, value = `google_service_account.archiviste_runtime.email`.
  - `GCS_SIGNING_PRIVATE_KEY_PEM` — plain env, value = `"placeholder-removed-by-SEC-004"` (inline sentinel string, no locals block needed). WHY comment ≥ 3 lines pointing to verified facts.
  - **No new `locals` block** — both placeholders are gone (public key now Secret Manager, GCS is an inline sentinel literal).
- `docs/runbook/bootstrap-gcp.md` — insert new section §5b (after §5 Mistral) titled *"Bootstrap JWT Ed25519 keypair + GCS signing placeholder"*. Two `gcloud secrets versions add` calls (one per key). Exact wording below.
- `CHANGELOG.md` — append entry under `## [Unreleased]`:
  - `### Fixed` → `INFRA-002: provision JWT Ed25519 keypair (both keys via Secret Manager) + GCS signing placeholder env vars on archiviste-gateway Cloud Run (unblocks gateway boot, AC-14).`

**Forbidden (NOT touched):** `gateway/**`, `workers/**`, `migrations/**`, `specs/**`, any new IAM role, any new SA.

## Migration order

None. No schema change. No `cargo sqlx prepare`.

## Test strategy (TDD order)

Pure HCL — TDD = static + dry-run:

1. `cd infra/terraform && terraform fmt -recursive -check` exits 0.
2. `cd infra/terraform && terraform validate` exits 0.
3. Operator `terraform plan -var-file=terraform.tfvars` shows exactly:
   - 2 new `google_secret_manager_secret` (jwt private + jwt public) (create).
   - 2 new `google_secret_manager_secret_iam_member` (create).
   - Update on `google_cloud_run_v2_service.gateway` adding the 4 env blocks (no other diff except expected revision metadata).
4. Operator runs §5b of runbook to push both secret versions (private + public).
5. Operator `terraform apply` → new gateway revision boots, `/healthz` returns 200.

No automated integration test added — pure infra change, validated by `terraform plan` review + post-apply curl (covered by AC-14 oracle).

## HCL snippets the implementer will write (close to final)

### `infra/terraform/secrets.tf` append

```hcl
# PR-e: JWT signing private key for archiviste-gateway (Ed25519).
# Version bootstrapped post-apply by operator (see docs/runbook/bootstrap-gcp.md §5b).
resource "google_secret_manager_secret" "jwt_ed25519_private_key" {
  secret_id = "JWT_ED25519_PRIVATE_KEY_PEM" # gitleaks:allow — Secret Manager resource ID, not a secret value
  labels    = local.labels

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "jwt_ed25519_private_key_accessor" {
  secret_id = google_secret_manager_secret.jwt_ed25519_private_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.archiviste_runtime.email}"
}

# PR-e: JWT verification public key (Ed25519). Routed through Secret Manager
# (mirror of private-key pattern) so rotation = `gcloud secrets versions add` only,
# no Terraform reapply, no operator paste into HCL. Public key is not secret material
# but the storage path is operationally symmetric and cheaper than maintaining a
# separate variable plumbing.
resource "google_secret_manager_secret" "jwt_ed25519_public_key" {
  secret_id = "JWT_ED25519_PUBLIC_KEY_PEM"
  labels    = local.labels

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "jwt_ed25519_public_key_accessor" {
  secret_id = google_secret_manager_secret.jwt_ed25519_public_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.archiviste_runtime.email}"
}
```

### `infra/terraform/cloud_run.tf` additions

No new `locals` entries. Inside `google_cloud_run_v2_service.gateway` → first `containers` block, after the existing `volume_mounts` block (~L79):

```hcl
      # PR-e: gateway boot contract (gateway/src/config.rs:58-87).
      # Both JWT keys via Secret Manager — verification key not strictly secret,
      # but symmetric storage = symmetric rotation (gcloud only, no terraform apply).
      env {
        name = "JWT_ED25519_PUBLIC_KEY_PEM"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.jwt_ed25519_public_key.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "JWT_ED25519_PRIVATE_KEY_PEM"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.jwt_ed25519_private_key.secret_id
            version = "latest"
          }
        }
      }

      # PR-e: GCS signing — interim placeholder for V1 unblock only.
      # Verified safe: Config::from_env (gateway/src/config.rs:81-84) does NOT parse
      # this PEM at boot — only `SecretString::from(env::var(...))`. The sole parse
      # site is `sign_get` (gateway/src/gcs/sign.rs:64-67), reachable only from the
      # signed-URL handler, which has no pre-auth route in V1 (auth tier MVP is
      # hardcoded anonymous per INFRA-002 non-goals). SEC-004 drops the field
      # entirely; this placeholder disappears with the refactor.
      env {
        name  = "GCS_SIGNING_SA_EMAIL"
        value = google_service_account.archiviste_runtime.email
      }

      env {
        name  = "GCS_SIGNING_PRIVATE_KEY_PEM"
        value = "placeholder-removed-by-SEC-004"
      }
```

Note: implementer must verify `local.labels` exists via grep before applying. Existing `mistral_api_key` references it (secrets.tf L5), so it exists somewhere — confirm and reuse identically.

## Runbook §5b amendment exact wording

Insert immediately after current §5 (before §6 pgvector):

````markdown
## 5b. Bootstrap JWT Ed25519 keypair + GCS signing placeholder

The gateway refuses to boot without 4 env vars (`gateway/src/config.rs:58-87`).
Terraform provisions the *plumbing* (2 Secret Manager secrets for the JWT keys +
an inline placeholder for the unused GCS signing field). The operator generates
the keypair and pushes both versions one-shot below.

### Generate the Ed25519 JWT keypair

```bash
openssl genpkey -algorithm ED25519 -out /tmp/jwt-private.pem
openssl pkey -in /tmp/jwt-private.pem -pubout -out /tmp/jwt-public.pem
```

### Push both keys to Secret Manager

```bash
gcloud secrets versions add JWT_ED25519_PRIVATE_KEY_PEM \
  --data-file=/tmp/jwt-private.pem
gcloud secrets versions add JWT_ED25519_PUBLIC_KEY_PEM \
  --data-file=/tmp/jwt-public.pem
```

Rotation: re-run the two `versions add` calls only. No `terraform apply` needed;
the next Cloud Run revision picks up `version = "latest"` automatically.

### Clean up local key material

```bash
shred -u /tmp/jwt-private.pem /tmp/jwt-public.pem
```

### GCS signing placeholder — DO NOT generate a real key

`GCS_SIGNING_PRIVATE_KEY_PEM` is set to the literal sentinel string
`"placeholder-removed-by-SEC-004"` directly in `infra/terraform/cloud_run.tf`.
`Config::from_env` performs no parsing at boot; the only PEM-parsing site
(`sign_get` in `gateway/src/gcs/sign.rs`) is unreachable in V1 (no pre-auth
route invokes the signer). SEC-004 drops the field entirely.
````

## Implementation steps (ordered)

1. Append the 4 resources (2 secrets + 2 IAM bindings) to `secrets.tf`.
2. Add the 4 `env` blocks to the gateway container in `cloud_run.tf` (no new locals).
3. `terraform fmt -recursive` then `terraform validate` from `infra/terraform/`.
4. Insert §5b in `docs/runbook/bootstrap-gcp.md`.
5. Add `## [Unreleased]` entry to `CHANGELOG.md`.
6. Operator review of `terraform plan` diff before any apply (human gate, not agent).

## Risks / open questions

- **`local.labels` reference** in the 2 new secrets: existing `mistral_api_key` uses it (L5), so it exists. Implementer confirms via `grep -r "^locals\|labels\s*=" infra/terraform/` before applying.
- **Sentinel-string compatibility with `Config::from_env`**: re-confirmed `std::env::var` returns the literal verbatim and `SecretString::from(String)` is a plain `From` impl with no validation. Zero added risk; flagged here for audit traceability only.

## Out of scope

- Any change to `gateway/src/config.rs` — Config struct stays as-is until **SEC-004** drops `gcs_signing_private_key_pem` and rewires signing via IAM `signBlob`.
- Any change to `workers/**`.
- Real GCS signing key provisioning (SEC-004).
- Per-environment (staging/prod) split of the JWT keypair (V1 single env).
- JWT key rotation tooling (`JWT_KID` already supports manual rotation; tooling deferred).
- Removing the per-secret IAM binding once project-wide role tightens (V2 SEC-001 SA split).
