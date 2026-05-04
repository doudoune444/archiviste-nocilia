<!-- Title format: <type>(<scope>): <subject>  e.g.  feat(gateway): FOUND-002 add /v1/retrieve handler -->

## Ticket

- ID : `<TICKET-ID>`
- Acceptance : `specs/acceptance/<ID>.md`
- Plan : `specs/plans/<ID>.md`
- Review : `specs/reviews/<ID>.md`

## Summary

<!-- 1–3 lines: what this PR delivers, end-to-end. -->

## Changes

<!-- Bulleted list of notable changes by area -->
- `gateway/` — ...
- `workers/` — ...
- `migrations/` — ...
- `specs/` — ...
- `docs/` — ...

## Acceptance criteria coverage

<!-- Copy ACs from specs/acceptance/<ID>.md and tick each -->
- [ ] AC-1 covered by `<test_path>::<test_name>`
- [ ] AC-2 covered by `<test_path>::<test_name>`
- [ ] AC-3 covered by `<test_path>::<test_name>`

## Test plan

- [ ] `cargo fmt --check` + `cargo clippy -D warnings` + `cargo test` green
- [ ] `ruff check` + `ruff format --check` + `mypy --strict` + `pytest` green
- [ ] Schemathesis run (if OpenAPI touched)
- [ ] Ragas eval (if RAG path touched) — score not regressed vs `eval/baseline.json`
- [ ] Property tests added/updated (if invariant touched in `specs/properties.md`)
- [ ] Manual smoke test : `<command or scenario>`

## Security checklist

- [ ] No secrets in diff (`gitleaks` clean)
- [ ] User input validated at trust boundaries
- [ ] Sensitive types wrapped (`secrecy::Secret<T>` Rust / `pydantic.SecretStr` Python)
- [ ] SQL parameterized (no string concat)
- [ ] Threat model row updated if new attack surface introduced
- [ ] Rate limit applied on new public route
- [ ] CORS / CSP headers verified if route exposed

## ADR

- [ ] No new heavy dependency / architectural decision, OR
- [ ] ADR added : `docs/adr/NNNN-<slug>.md`

## CHANGELOG

- [ ] Entry added under `## [Unreleased]` in `CHANGELOG.md`, OR
- [ ] release-please will generate (commits are conventional)

## Out of scope

<!-- Explicitly list what this PR does NOT do, deferred to future tickets -->

## Risks / rollback

<!-- Migration safety, feature flag status, rollback steps if needed -->

---
**Reviewer note** : `/review <ID>` runs the adversarial review agent. Verdict must be `APPROVE` before merge.
