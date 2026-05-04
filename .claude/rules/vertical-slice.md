# Vertical slice

One PR = one ticket = end-to-end change.

## Size
- ≤ 300 LOC diff (excluding migrations and generated files).
- Beyond → split ticket. Tell architect / human.

## Order
1. Migration first (if schema change). Run `cargo sqlx prepare` after.
2. Integration test that fails (TDD). Test references AC explicitly in comment.
3. Implementation until test passes.
4. Property test if `specs/properties.md` lists relevant invariant.
5. CHANGELOG entry under `## [Unreleased]`.
6. OpenAPI update + schemathesis run if contract touched.

## Scope
- Touch only files listed in `specs/plans/<ID>.md` "Files to touch".
- Out-of-scope refactor → new ticket. Don't piggyback.
