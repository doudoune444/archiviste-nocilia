# Review — OPS-001a

## Verdict

APPROVE

## Scope honored

Split sub-ticket OPS-001a covers AC-1, AC-2, AC-3, AC-4, AC-5, AC-6 (structure-only).
AC-7, AC-8, AC-9 properly deferred to OPS-001b — clearly marked `PENDING LIVE DEPLOY (OPS-001b)` in the skeleton report. Split decision documented at top of `specs/plans/OPS-001.md` (lines 3-7).

## Lint + test status

| Check | Result |
|---|---|
| `cargo fmt --check` | pass |
| `cargo clippy --all-targets -- -D warnings` | pass (0 warnings) |
| `cargo test --test overhead_header_test` | 5/5 pass |
| `cargo test` (full) | OPS-001a tests green; 4 pre-existing dashboard test failures unrelated (`test_dashboard_backend.rs` untouched, failures are sqlx-test DB connection — "Hôte inconnu", env issue not regression) |

## AC coverage

| AC | Status | Evidence |
|---|---|---|
| AC-1 | covered | `scripts/load/k6/chat-round-trip.js:53-75` — `chat_100_users` + `chat_500_users` scenarios, `ramping-vus`, 30 s ramp + 60 s steady, selection via `--env SCENARIO=` (line 41, 79-81) |
| AC-2 | covered | `chat-round-trip.js:138-148` — POST `/v1/chat` JSON `{conversation_id, query}` from `prompts.json` (10 entries `prompts.json:1-12`) |
| AC-3 | covered | `chat-round-trip.js:100-108` — 4 thresholds wired: p95<3000 per scenario, http_req_failed<0.01, gateway_overhead_ms p95<80 on chat_500_users |
| AC-4 | covered | `chat-round-trip.js:88-91, 156-167` — Trend `gateway_overhead_ms` from `X-Gateway-Overhead-Ms`, Counter `gateway_overhead_header_missing` + warn on absent. Tagged `{scenario}`. Tests `overhead_header_test.rs::ac4a/b/c/d/e` cover header semantics |
| AC-5 | covered | `scripts/load/README.md` — sections (a) pre-req, (b) commands, (c) budget table, (d) summary export, (e) report procedure |
| AC-6 (structure) | covered | `docs/load-test-report-v1.md` — all named sections (metadata, metrics, SLO verdicts, cold-start, budget, Cloudflare, Langfuse, follow-ups), TBD placeholders, explicit STATUS line 3 |
| AC-7 | deferred | report section line 82-90 marked `*Filled by OPS-001b.*` |
| AC-8 | deferred | report section line 103-111 marked `*Filled by OPS-001b.*` |
| AC-9 | deferred | report section line 94-99 marked `*Filled by OPS-001b.*` |

## AC-4 test sub-path coverage (plan §37)

| Sub-path | Test | File:line |
|---|---|---|
| (a) header on 200 | `ac4a_overhead_header_present_on_200` | `overhead_header_test.rs:62-80` |
| (b) valid integer | `ac4b_overhead_header_is_integer` | `:88-109` |
| (c) overhead < total elapsed | `ac4c_overhead_less_than_total_elapsed` | `:118-146` |
| (d) gap ≈ workers delay | `ac4d_overhead_gap_approximates_workers_delay` | `:155-191` |
| (e) header on 400 | `ac4e_overhead_header_present_on_400` | `:200-210` |

All 5 sub-paths covered. No skipped/ignored tests.

## Findings

### HIGH

None.

### MED

| File:line | Pattern | Evidence | Suggested fix |
|---|---|---|---|
| `scripts/load/README.md:47` | budget table inconsistency | 500-user run estimate ~€100 exceeds hard cap €30/run (D-3, line 52). Operator running 500-VU at full duration WILL trip the cap mid-run (failure mode documented in spec line 59) | Add explicit note "500-user run at full duration exceeds €30 cap — abort or split into shorter slice; or raise cap with human sign-off". This is an OPS-001b concern but the README is what OPS-001b operator reads |

### LOW

| File:line | Pattern | Evidence | Suggested fix |
|---|---|---|---|
| `scripts/load/k6/chat-round-trip.js:135-136` | comment / code mismatch | comment says "round-robin by VU iteration counter" but code uses `Math.random()` | either change comment to "random" or implement actual round-robin via `__ITER` |
| `scripts/load/k6/chat-round-trip.js:80` | per-iteration object allocation | `activeScenarios` rebuilt at module-load time (only once) — fine; cosmetic. Disabled scenario uses placeholder `constant-vus vus:0` which still creates an executor entry. k6 accepts this idiom | none — acceptable |
| `gateway/src/handlers/chat.rs:188-190` | extension write under multiple early-returns | `cell.set()` runs only after the awaited result returns through `match`. On panic mid-await the slot stays 0 → middleware reports overhead = total. Not a correctness issue (overhead just overestimates by ~workers time on panic-during-fwd); document or accept | accept; document in `timing.rs` rustdoc |

## Design deviation analysis (plan §32 — Layer vs from_fn)

Plan §32 specified custom `tower::Layer` `OverheadHeaderLayer`; implementer used `axum::middleware::from_fn` with a shared `Arc<AtomicU64>` request-extension cell.

**Justification (timing.rs:12-13)**: "Using a request-extension atomic cell avoids relying on response-extension propagation across Axum's `MapIntoResponse` wrappers."

**Verdict**: deviation accepted. `axum::middleware::from_fn` returns `Response` directly after `next.run(req).await`, allowing direct `headers_mut().insert()` post-await without `Service`/`Layer` trait gymnastics. The shared `Arc<AtomicU64>` slot pattern (inserted in request extensions at middleware entry, written by handler, read by middleware after `await`) is concurrency-correct:

- Per-request allocation (`WorkersCallDuration::new()` line 77 — new `Arc` per request, no cross-request leak).
- `Release`/`Acquire` ordering pair (line 43, 49) — correct happens-before between handler write and middleware read.
- Fallback when handler never wrote: `saturating_sub` yields 0 if `workers > total` (impossible in practice) and `total` (no workers call) when slot is 0. Matches plan §39 "Header posé quand même".

The `Option<Extension<WorkersCallDuration>>` in `chat.rs:75` correctly handles the test path where the middleware is not applied (non-chat routes).

## Concurrency & safety review

| Aspect | Status |
|---|---|
| `Arc<AtomicU64>` shared across request lifecycle | safe — Release/Acquire ordering correct |
| Per-request slot allocation | confirmed line 77 — new instance per request |
| `unwrap_or(u64::MAX)` on `as_nanos()` / `as_millis()` | saturating fallback, no panic. Comment line 41 justifies (overflow > 584 years = irrelevant) |
| Header construction `HeaderValue::from_str` | `if let Ok` — silently drops header if u64 cannot serialise as ASCII (impossible for decimal digits but defensive). Acceptable |
| `OVERHEAD_HEADER` constant lower-case | matches HTTP/2 normalization, k6 client extracts via case-insensitive `res.headers["X-Gateway-Overhead-Ms"]` (line 157) — verified |

## Security audit (per `.claude/rules/security.md`)

| Check | Status | Notes |
|---|---|---|
| Secrets in diff | clean | no API keys, tokens, JWTs, connection strings |
| Sensitive type wrapping | N/A | no new secret material introduced |
| SQL injection | N/A | no SQL touched |
| Path traversal | N/A | no filesystem ops |
| SSRF | N/A | middleware reads `Instant`, posts no URL. k6 script uses fixed `TARGET_URL` from `__ENV`, default hardcoded prod hostname — not user-supplied at runtime |
| JWT misuse | N/A | not touched |
| CORS / CSP / HSTS | unchanged | preserved from SEC-003 (lib.rs:313-318, 372-391) |
| Rate limit on `/v1/chat` | unchanged | not in OPS-001a scope (tower_governor TBD) |
| Unbounded input | unchanged | `RequestBodyLimitLayer::new(1_048_576)` preserved on chat router (lib.rs:325) |
| Trust boundary | unchanged | `x-user-tier` / `x-user-id` still propagated from `AnonIdentity` extension (chat.rs:175-176), not user body |
| Header injection (overhead header) | safe | value derived from `u64::to_string()` → decimal digits only, ASCII-safe, no CRLF risk |
| Prompts content | clean | `prompts.json` — 10 in-domain canon questions, no PII, no instruction-override patterns, no zero-width chars |
| k6 secret leak | clean | no env vars, no auth headers, no tokens; TARGET_URL is a public domain |
| Cloudflare bypass procedure | well-documented | README explicitly mandates removal post-run (line 12) |

## Vertical-slice budget

`git diff main...HEAD --stat`: **530 insertions, 9 deletions** across 11 files.

Per-category breakdown:

| Category | LOC | Counted? |
|---|---|---|
| `gateway/src/middleware/timing.rs` | 94 | yes (prod) |
| `gateway/src/middleware/mod.rs` | 4 | yes (prod) |
| `gateway/src/handlers/chat.rs` | 27 | yes (prod) |
| `gateway/src/lib.rs` | 15 (mostly re-indent) | yes (prod) |
| `gateway/tests/overhead_header_test.rs` | 210 | test |
| `scripts/load/k6/chat-round-trip.js` | 174 | excluded per plan §6 (test artifact, analogous to generated files) |
| `scripts/load/k6/prompts.json` | 12 | excluded per plan §6 |
| `scripts/load/README.md` | 83 | doc |
| `docs/load-test-report-v1.md` | 121 | doc |
| `CHANGELOG.md` | 4 | doc |
| `specs/plans/OPS-001.md` | 5 | doc |

**Prod Rust = 140 LOC**, **prod + tests = 350 LOC**.

Plan §6 explicitly excludes k6 scripts and prompts.json from the budget as test artifacts. With that exclusion accepted: prod ≈ 140 + tests 210 + doc 213 = within reasonable single-PR scope. Approved.

## Out-of-scope changes

None. All files match plan §20-30 "Files to touch":
- `gateway/src/middleware/timing.rs` ✓
- `gateway/src/middleware/mod.rs` ✓
- `gateway/src/lib.rs` ✓
- `gateway/src/handlers/chat.rs` ✓ (write to slot — implicit in plan §32 design)
- `gateway/tests/overhead_header_test.rs` ✓
- `scripts/load/k6/*` ✓
- `scripts/load/README.md` ✓
- `scripts/load/runs/.gitkeep` ✓
- `docs/load-test-report-v1.md` ✓
- `CHANGELOG.md` ✓
- `specs/plans/OPS-001.md` (split decision amendment — documented top of file)

## Property invariants

No invariant from `specs/properties.md` is relevant to a request-timing middleware (plan §40 confirmed). N/A.

## Observability

- New code path adds tracing? No new event — overhead is exposed via response header. Acceptable for AC-4 scope.
- Log line `chat` already includes `latency_ms` (chat.rs:287-294). No regression.

## Recommendation

APPROVE for merge to `main`. The single MED finding (budget table inconsistency in README) is an operator-facing doc nit that affects OPS-001b execution, not OPS-001a correctness — non-blocking but worth fixing in a follow-up tiny patch or addressed in OPS-001b directly.

The implementer's deviation from plan §32 (`from_fn` vs `tower::Layer`) is technically justified and architecturally clean. The `Arc<AtomicU64>` slot pattern is correctly scoped per-request with proper memory ordering.
