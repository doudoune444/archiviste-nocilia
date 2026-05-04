# Secret hygiene

## Never commit

Files / directories:
- `.env`, `.env.local`, `.env.production` (only `.env.example` versioned).
- `*.key`, `*.pem`, `*.p12`, `*.pfx`.
- `id_rsa`, `id_ed25519`, `id_ecdsa` (any SSH private key).
- `*-sa.json`, `service-account*.json` (GCP service account keys).
- `*.tfvars`, `terraform.tfstate`, `terraform.tfstate.backup` (Terraform state = plain-text secrets).
- `kubeconfig`, `kubeconfig.*`, `*.kubeconfig`.
- `.npmrc`, `.pypirc` if they contain `_authToken=` / `password=`.
- `secrets/**` directory.
- Any file containing API keys, tokens, JWTs, DB passwords, connection strings.

Inline content patterns (catch via `gitleaks` / `detect-secrets`):
- Long high-entropy strings (Shannon entropy > 4.5 over 20+ chars).
- `AKIA[0-9A-Z]{16}` (AWS access key).
- `xox[baprs]-[0-9a-zA-Z-]+` (Slack token).
- `gh[pousr]_[A-Za-z0-9]{36,}` (GitHub PAT).
- `ya29\.[0-9A-Za-z\-_]+` (Google OAuth token).
- `eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+` (JWT).
- `postgres://`, `mysql://`, `mongodb://` with embedded password.

## Defense in depth

1. **`.gitignore`** covers patterns above.
2. **PreToolUse Write hook** (`guard-secret-paths.sh`) blocks agent writes to these paths.
3. **`gitleaks` / `detect-secrets`** pre-commit hook scans content.
4. **CI**: `gitleaks detect --source . --no-git` on every PR.

## Production
- All secrets via GCP Secret Manager.
- Workers/gateway read at boot via `gcp-secret-manager` SDK or env vars injected by Cloud Run.
- No fallback default in code (`os.getenv("API_KEY", "default")` forbidden).

## Tests
- Use fixtures, never real credentials.
- Mock external APIs. No live calls in unit/integration tests.

## On accidental commit
1. Rotate secret immediately.
2. Force-rewrite history is **not enough** — assume leaked.
3. Tell human. Do not hide.
