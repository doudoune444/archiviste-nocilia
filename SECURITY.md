# Security Policy

This is a solo portfolio project. There is no SLA. Reports are handled best-effort by a single maintainer.

## Reporting a vulnerability

**Do not open a public GitHub issue for security findings.**

Use one of these private channels:

1. **Preferred** — GitHub Security Advisories: "Report a vulnerability" button on the repo's `Security` tab (private).
2. **Email** — `baptiste.herbecq@gmail.com` with subject prefix `[SECURITY] archiviste-nocilia`.

Please include:

- Affected component (gateway / workers / infra / docs)
- Reproduction steps or proof-of-concept
- Impact assessment (data exposure, availability, integrity)
- Suggested mitigation if known

PGP not required, accepted on request. I'll acknowledge as soon as I can and follow [coordinated disclosure](https://en.wikipedia.org/wiki/Coordinated_vulnerability_disclosure) practices.

## Scope

In scope: `gateway/`, `workers/`, `migrations/`, `infra/`, `.github/workflows/`.

Out of scope:

- Third-party dependencies — please report upstream (I monitor `cargo-deny` + `pip-audit`).
- Self-hosted instances misconfigured by operators.
- Issues requiring physical access to deployment infrastructure.

## Internal practices

- OWASP Top 10 + LLM threats — [`.claude/rules/security.md`](.claude/rules/security.md)
- STRIDE threat model — [`specs/threat-model.md`](specs/threat-model.md)
- Pre-commit secret scanning (`gitleaks`, `detect-secrets`)
- Dependency auditing in CI (`cargo-deny`, `pip-audit`)
- Secrets via GCP Secret Manager — none in repo or images
