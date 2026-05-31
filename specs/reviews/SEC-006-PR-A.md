# Review — SEC-006 PR-A (gateway ID-token signing)

Branch: `feat/SEC-006-pr-a-id-token-signing` · Commit: `9d809e1` · Base: `main` (`acebaee`)
Diff: 11 files, +967 / -20. `cargo fmt --check` clean. `cargo clippy --all-targets -- -D warnings` clean. 8 unit tests + 4 integration tests for SEC-006 all green locally.

## Verdict

**REQUEST_CHANGES** — no security issue, no gaming, AC coverage essentially complete, but three concrete fix-before-merge items: (1) unit-test gap on the 5th declared fallback reason (`exp_not_numeric` is implemented and listed by AC-3 but has no test); (2) signature drift between spec wording and the production API (`fetch_id_token()` takes `&self`, no audience argument — spec says `fetch_id_token(audience: &str)`); (3) two integration cases use the SAME mockito server URL for `audience` AND for the metadata server URL, which weakens the test as a contract check and masks a potential audience-vs-metadata-URL confusion. None blocking; flip to APPROVE once addressed (or accepted with WHY comments).

## Critical issues (must-fix before merge)

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| `gateway/src/auth_metadata/id_token.rs:228-243` | MED-HIGH | spec violation — missing test | `decode_exp_from_payload` returns `"exp_not_numeric"` (line 242), enumerated by spec AC-3 as one of the five fallback reasons. No unit test exercises this branch. Plan §AC-3 says "5 fallback reasons" but only declares 4 sub-tests (b..e). Coverage gap confirmed by `rg exp_not_numeric gateway/` returning only 2 prod-code hits. | Add `exp_fallback_on_exp_not_numeric` test: payload like `{"sub":"x","exp":"not-a-number"}` (string instead of int). One test, ~15 LOC. |
| `gateway/src/auth_metadata/id_token.rs:142` + `state.rs:93` | MED | spec wording violation (AC-1 / AC-2) | Spec AC-1: "expose `fetch_id_token(audience: &str)`". Spec AC-2 (and oracle): `fetch_id_token` is called per-call with an audience. Production signature is `pub async fn fetch_id_token(&self) -> Result<SecretString, TokenError>` — audience captured as a field at ctor time (`with_audience`). Plan §"Files to touch" PR-A actually documents both forms (`with_audience(audience: String)` ctor AND `fetch_id_token(audience: &str)` method), so plan itself is internally inconsistent. The implementation is one valid resolution of that inconsistency, and it matches the AppState usage (one provider per audience), but the spec contract is technically not satisfied. | Either (a) amend spec AC-1/AC-2 to "audience captured at ctor; fetch is parameterless" (one-line spec edit, requires human approval per CLAUDE.md §Sources de vérité), or (b) change signature to `fetch_id_token(&self, audience: &str)` and assert at runtime that it matches `self.audience` (ugly). Recommend (a). |
| `gateway/tests/test_chat_workers_auth.rs:100,133,161,173,263,289` | MED | test smell — audience aliased to metadata URL | In three of the four integration tests, `workers_url_for_audience = meta_server.url()`. The audience passed to `IdTokenProvider` is the URL of the *mock metadata server*, not the URL of the *mock workers server*. The percent-encoded `?audience=` in the mock route then happens to encode the metadata URL. This works for the test but: (a) does not exercise the production invariant "audience = WORKERS_URL"; (b) would silently pass if production swapped the two; (c) makes the test self-referential. The mockito URL also includes a random port, so the test is a moving target rather than a contract check. | Pass the workers mock URL as audience: `workers_url_for_audience = workers_server.url()`. Update the `identity_path()` builder to receive the workers URL. Same 6 line changes, clearer intent. |

## Major issues (should-fix this PR)

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| `gateway/src/auth_metadata/id_token.rs:255-275` | MED | premature abstraction / DRY ambiguity | `percent_encode_audience` (16 LOC) + `hex_nibble` (3 LOC) re-implement a percent-encoder from scratch. The same function is duplicated verbatim in `gateway/tests/test_chat_workers_auth.rs:51-67`. `reqwest` is already a direct dep and exposes `.query(&[("audience", audience)])` which would produce the exact same encoding for these characters. Clean-code rule: "No premature abstraction. Three similar lines beats a generic helper" and "No over-configurability. Hardcode until a second caller appears." | Replace `format!(... ?audience={})` + `percent_encode_audience` with `self.client.get(format!("{}{}", base, path)).query(&[("audience", audience)])`. Drop the helper + duplicate. Saves ~19 prod LOC + ~17 test LOC. The mockito match string `audience=http%3A%2F%2F...` is what reqwest produces. Test by running `cargo test fetch_id_token_calls_identity_endpoint`. |
| `gateway/src/auth_metadata/id_token.rs:87-107` | MED | plan deviation / scope creep | `new_stub_always_valid` (21 LOC, gated `#[cfg(any(test, feature = "test-utils"))]`) was added unplanned. Plan §"Files to touch" lists only `with_audience` + `with_base_url_and_audience`. The stub exists solely so 5 existing test files do not have to spin up a mockito metadata server. Acceptable but ugly — it ships a fake bearer string in a public-ish constructor that other tests could later misuse. | Either keep with a WHY comment justifying it (~1 LOC), OR replace with a single test helper `make_app_state(workers_url)` (already exists in `jwt_helpers.rs:130`) that constructs a real `IdTokenProvider` against a one-shot mockito server — but the current `make_app_state` already calls `new_stub_always_valid`, so the helper would just move complexity sideways. Recommend keeping the stub with a 2-line WHY comment expanding on "tests that assert on the actual bearer value or on the metadata server call count should NOT use this ctor" → make that a `#[must_use]`-style enforcement (e.g., return a wrapper type) only if a 4th similar stub appears. Net: leave it, document why. |
| `gateway/tests/test_chat_workers_auth.rs:259-310` | MED | test smell — cache-hit assertion strength | The "cache hit" test asserts `meta_mock.expect(1)`. mockito's `expect(1)` allows exactly one match across the test lifetime; the test makes two `POST /v1/chat` calls. If the cache were broken (i.e., 2 metadata fetches), the test would fail with "expected 1, got 2". So the test DOES prove caching — but only weakly because both calls happen on the same `AppState` clone (same `Arc<IdTokenProvider>`), which is the trivial case. A stronger test would also verify that the JWT body returned in the second response is byte-identical to the first (proves same cached secret), not just that the metadata count is 1. | Add: `assert_eq!(resp1_body, resp2_body)` after both responses. ~3 LOC. |
| `gateway/tests/test_chat_workers_auth.rs:243-249` | MED | brittle log assertion | The timeout test logs `assert!(logs_contain("timeout") || logs_contain("network"))`. The OR clause exists because reqwest may classify a stalled-accept-no-response as either depending on OS scheduling. This makes the test a pass even if the production `classify_reqwest_error` mapping is fundamentally broken (it would still log SOMETHING). Stricter test would use `tokio::time::pause()` + manual timeout, but that's a bigger refactor. | Acceptable as-is given the comment ("Either timeout or network depending on OS"). Lower to LOW after re-reading. |
| `gateway/src/handlers/chat.rs:160-179` | LOW-MED | clean-code — fn growth | `forward_to_workers` body now ~70 lines (was ~45). Clean-code rule: ≤40 lines. The new ID-token fetch block (lines 158-179, ~22 LOC) is the largest single new chunk. | Extract `fetch_id_token_or_503(state, request_id, start) -> Result<SecretString, (StatusCode, Option<u16>, Response)>` (~25 LOC helper, brings `forward_to_workers` body back to ~48 lines). Not a regression vs `main` boundary since it was already over the cap, but PR-A made it worse. |

## Minor issues (nice-to-have)

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| `gateway/src/auth_metadata/id_token.rs:88-93` | LOW | dead constants in stub ctor | `new_stub_always_valid` constructs a reqwest client with the real CONNECT/TOTAL timeouts even though the client will never be used (the cache is pre-seeded with `expires_at = now + 24h`). Builds a `Client` for nothing. | Build a default `Client::new()` or skip — minor LOC saving. |
| `gateway/src/handlers/chat.rs:160` | LOW | unused timing | `let id_token_start = Instant::now();` is computed but `latency_ms` from this timer is only emitted on the error path. On the success path, the ID-token fetch latency is silently rolled into the workers call duration. | Either emit a success-path `tracing::debug!(event="chat.id_token_fetched", latency_ms)` (overkill phase 1) or accept the loss. Mention in WHY comment that the timer is for the error-path log only. |
| `gateway/src/auth_metadata/id_token.rs:202-211` | LOW | redundant warn-log site | The `missing_segment` branch emits the warn log inline (lines 206-209), whereas the other 4 branches funnel through `decode_exp_from_payload` returning a `&'static str` reason consumed by line 216. Two slightly different log paths for the same event. | Have `parse_jwt_exp` only own the log emission (single call site). Move the `missing_segment` detection into `decode_exp_from_payload` (take `&str` jwt, return `Result<i64, &'static str>` after splitting). Saves 4 LOC + unifies the log site. |
| `gateway/src/auth_metadata/id_token.rs:55` | LOW | stale rustdoc | Doc says "each audience should have its own instance (AC-1 / non-goals)" — AC-1 reference is correct, but the actual API (`fetch_id_token(&self)` no audience arg) makes this a *hard requirement* (one provider = one audience), not a recommendation. | Reword: "Each instance is bound to exactly one audience set at construction time." |
| `gateway/src/state.rs:212-244` | LOW | ctor proliferation | `AppState` now has 8 constructors. `new`, `new_with_pool`, `new_with_pool_and_sql_token_provider`, `new_with_lookup`, `new_with_mocks`, `new_with_all_mocks`, `new_with_token_provider`, `new_with_id_token_provider`, `new_with_pool_and_token_provider`. SEC-006 added one. This is debt across the SEC-* series, not a PR-A regression, but worth flagging for a follow-up refactor (builder pattern). | Out of scope for PR-A. New ticket. |
| `gateway/src/auth_metadata/id_token.rs:11-14` | LOW | AC tags in module doc | Comments cite AC-1..AC-4 by number. If the spec ever renumbers these, the doc lies silently. | Acceptable convention in this codebase (already used in chat.rs). No change. |

## AC compliance table

| AC   | Status   | Evidence |
|------|----------|----------|
| AC-1 | **PASS** | `pub struct IdTokenProvider` present in `auth_metadata/id_token.rs:56`; re-exported `mod.rs:10`; field added `state.rs:71`; `git diff main -- gateway/src/auth_metadata/token.rs` = 0 lines (TokenProvider strictly untouched). |
| AC-2 | **PARTIAL** | Endpoint path `METADATA_IDENTITY_PATH` correct; `Metadata-Flavor: Google` header set; timeouts reused from `token.rs::{CONNECT_TIMEOUT_SECS, TOTAL_TIMEOUT_SECS}`. Audience captured at ctor (field) instead of per-call (`fetch_id_token(audience: &str)` per spec wording) — see Critical #2. The HTTP contract is correct; the function signature deviates from spec wording. |
| AC-3 | **PARTIAL** | 4 of 5 fallback reasons unit-tested (`missing_segment`, `b64_decode`, `json_decode`, `missing_exp`). 5th reason `exp_not_numeric` is implemented (`id_token.rs:242`) but has no test — see Critical #1. Bearer JWT never logged (verified — only `reason` field emitted). |
| AC-4 | **PASS** | `REFRESH_AHEAD_SECS = 60` (line 33); read-lock fast-path + write-lock double-check copies `token.rs` pattern (lines 144-162). Two tests: `cache_hit_skips_metadata_fetch` (`expect(1)`) + `cache_refresh_ahead_window` (`expect(2)` with `exp = now+30s`). |
| AC-5 | **PASS** | `workers_id_token_provider: Arc<IdTokenProvider>` on `AppState` (line 71); instantiated via `IdTokenProvider::with_audience(config.workers_url.clone())` in `AppState::new` (line 93) and three other prod-ish ctors. No composite struct. |
| AC-6 | **PASS** | Fetch in `chat.rs:161`; bearer attached via `.bearer_auth(...)` at line 202; error → 503 `upstream_unavailable` at line 172-177; warn log `event="chat.id_token_failed"` at lines 166-171 with fields `{request_id, latency_ms, reason_code}`. `reason_code` mapping in `classify_id_token_error` (lines 369-375) covers exactly `{metadata_token_failed, timeout, network}`. NO `bearer`/`id_token`/`audience` field logged (verified by reading the `warn!` macro args). Integration test asserts log content for `metadata_token_failed`. |
| AC-7 | **PASS** | Workers mock at `test_chat_workers_auth.rs:118-120` uses `match_header("Authorization", Matcher::Regex(format!("^Bearer {}$", regex::escape(&jwt))))` — stricter than the AC-7 baseline `^Bearer .+$`; pinned to the exact JWT string. |
| AC-8 | **PASS** | `rg 'workers_id_token_provider' gateway/src/lib.rs` returns 0 lines (confirmed). The only call site is `chat.rs:161`. No boot warm-up. |

## Property invariants

`specs/properties.md` lists no invariant related to outbound auth (plan confirms). N/A. Existing `chat_property_test.rs` updated to inject the stub provider — no new property test required.

## Security audit

| Check | Status | Note |
|---|---|---|
| Secrets in code | OK | No hardcoded credentials. Stub bearer `"stub-id-token-for-tests"` is a literal sentinel, not a real token. |
| `SecretString` wrap | OK | `CachedIdToken.bearer: SecretString` (line 45); `fetch_id_token` returns `SecretString`; exposed only at the `.bearer_auth` call site via `secrecy::ExposeSecret::expose_secret` (chat.rs:202). |
| JWT logged | OK | Confirmed by reading both `tracing::warn!` macro calls — only `event`, `reason`, `request_id`, `latency_ms`, `reason_code` fields. No `bearer`, no `payload`, no `audience`. |
| SSRF (A10) | OK | Metadata URL is constant. `audience` is `config.workers_url` (trusted env) — never user input. Custom percent-encoder produces only ASCII (`%XX` form), so cannot inject `\r\n` request smuggling. |
| `unwrap()` / `expect()` on user-derived input | OK | None. The only `unwrap_or_else(fallback_expires_at)` is on `DateTime::from_timestamp` of a numeric epoch — safe by construction (`from_timestamp` only fails on out-of-range i64, fallback covers it). |
| `unsafe` blocks | OK | None. |
| TLS bypass (`verify=False`) | OK | None. |
| JWT signature verification | N/A | Per spec non-goal: gateway trusts the HTTPS connection to the metadata server, not the JWT signature. Documented in module-level doc lines 198-199. |
| CORS / CSP / rate limit | N/A | No new public route. |

No security finding. The `secrecy` wrapping is conscientious; the audit trail (log shape) is correct.

## Out-of-scope changes

None detected. All 11 touched files are listed in plan §"Files to touch — PR-A" (the 5 existing test files modified are implicit dependencies of the `AppState::new` signature change — the plan acknowledges "extend test ctors" in `state.rs` line of work, but does not explicitly enumerate the 5 test files. Acceptable.).

## LOC reduction opportunities (concrete, with estimated savings)

Total diff +967 / -20 = far above the 300 LOC vertical-slice cap. Spec §"Effort estimate" pre-acknowledged the overshoot and split the work into PR-A + PR-B; plan PR-A target was "~245 LOC logical". Actual production-only LOC (excluding tests) is ~386. The overshoot is mostly tests (~605 of the 967 added), which are correct to keep. Concrete reductions:

| Item | File | Estimated LOC saved | Risk |
|---|---|---|---|
| Drop `percent_encode_audience` + `hex_nibble` + duplicate in tests; use `reqwest.query(&[...])` | `id_token.rs:255-275` + `test_chat_workers_auth.rs:51-67` | ~36 (19 prod + 17 test) | Low — reqwest produces identical encoding for unreserved chars. Re-run `fetch_id_token_calls_identity_endpoint` to confirm `audience=http%3A%2F%2F...` byte-match. |
| Inline `decode_exp_from_payload` into `parse_jwt_exp`; unify log call site | `id_token.rs:203-243` | ~10 | Low |
| Replace `new_stub_always_valid` with helper that constructs a real `IdTokenProvider` against an embedded mockito; OR keep stub, drop unused reqwest client construction | `id_token.rs:87-107` | ~5-15 | Low (just the unused client builder) |
| Test (a)/(b)/(c) audience plumbing: pass workers URL, not metadata URL | `test_chat_workers_auth.rs` | 0 net (semantic clarity, not LOC) | Low |

Total realistic shrinkage: **~50 LOC**, bringing prod-only diff from ~386 to ~336. Still above 300. The remaining overshoot is justified (full cache + 5-case fallback + 4 integration cases is the minimum to cover SEC-006). Recommend accepting the overshoot once Critical #1 + #3 fixed; do NOT split into PR-A1 + PR-A2 (would split the cache from its fetch logic, ugly).

## Summary

PR-A is functionally correct, security-clean, and lint-clean. All 12 tests pass. The three Critical items (missing `exp_not_numeric` test, `fetch_id_token` signature drift vs spec, audience=metadata-URL aliasing in 3 integration tests) are low-effort fixes; address them and the PR ships. The LOC overshoot (+947 prod+test) is acknowledged by spec — a ~50 LOC trim via dropping the custom percent-encoder is worth doing but not blocking. Bearer hygiene (`SecretString`, no JWT in logs) is correct. AC-8 (no boot warm-up) confirmed by grep.
