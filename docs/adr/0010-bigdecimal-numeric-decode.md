# ADR-0010 — `bigdecimal` crate for `NUMERIC(5,4)` decoding (OBS-004)

**Status**: Accepted
**Date**: 2026-06-08
**Ticket**: OBS-004

## Context

`GET /v1/quality` reads `faithfulness`, `answer_relevancy`, `context_precision`, and
`context_recall` from `eval_runs.NUMERIC(5,4)` columns (OBS-003 migration).
AC-3 requires the values to be serialised as JSON numbers equal to the stored
`NUMERIC` value without rounding.

`sqlx 0.8` does not support decoding PostgreSQL `NUMERIC` into `f64` (the type is
not registered) and decoding into `f32`/`f64` would introduce binary floating-point
rounding that violates AC-3 (e.g. `0.9234` stored as `NUMERIC` could become
`0.9233999...` as `f64`).

## Decision

Add the `bigdecimal` feature to the `sqlx` dependency and add the `bigdecimal = { version = "0.4", features = ["serde"] }` crate.

`sqlx` natively decodes PostgreSQL `NUMERIC` → `BigDecimal` when the `bigdecimal`
feature is enabled. With the `serde` feature, `serde_json` serialises `BigDecimal`
as a JSON number (not a quoted string), preserving the exact decimal representation.
A stored value of `0.9234` serialises to the JSON number `0.9234`.

## Alternatives rejected

- **(b) `::float8` cast in SQL** — forces rounding at the DB layer; a stored
  `NUMERIC(5,4)` `0.9234` may not round-trip as the exact JSON number `0.9234`.
  Violates AC-3.
- **(c) `::text` + re-parse gateway-side** — brittle (locale, trailing zeros,
  scientific notation edge cases). Requires unsafe `from_str` → `f64` which reintroduces
  floating-point imprecision. Violates AC-3 and `no-workaround.md`.

## Consequences

- One new production crate: `bigdecimal 0.4`. It has no FFI, no C deps, and is under
  1k LOC of Rust. `cargo deny check` passes (MIT/Apache-2.0 licence, no known advisories).
- `sqlx` gains one feature flag (`bigdecimal`). The `dev-dependencies` sqlx block also
  gains the flag so that `#[sqlx::test]` can insert and round-trip `NUMERIC` values.
- The handler struct uses `bigdecimal::BigDecimal` for the four metric fields; all
  other types in the handler remain unchanged.
