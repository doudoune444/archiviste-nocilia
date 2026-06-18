# Review — FIX-SIGNAL-173

Reviewed commit: `45f1e28` `fix(signal): #173 distinct skipped_error feedback on send-anyway`.
NOTE: `git diff main...HEAD` includes the full #172 change set because #172 (`bf96ca4`, merge PR #209) is NOT yet on `main`. The #172 noise (workers, openapi, #172 plan) was excluded from this review — only the isolated #173 impl commit was audited. Confirmed via `git merge-base --is-ancestor bf96ca4 main` → not on main.

## Verdict
APPROVE

## Findings

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| — | — | — | No HIGH/MED/LOW findings in the #173 impl commit. | — |

The isolated #173 diff (`git show 45f1e28`) touches exactly the 3 planned files: `gateway/static/assets/app.js` (+8), `gateway/tests/test_report_contradiction.rs` (+42), `CHANGELOG.md` (+2). No out-of-scope files in the impl commit.

## Gaming audit
| Check | Result |
|---|---|
| New `else if (action === "skipped_error")` fires before generic `else` | OK — app.js:293 precedes `else` at :301; explicit string match |
| `skipped_error` mis-routes | No — only `created`/`incremented` (`:286`) and `skipped_error` (`:293`) are matched; everything else falls to generic `else` |
| Generic `else` still catches unexpected actions | OK — app.js:301-304: recoverable copy + BOTH buttons re-enabled (`signalerSendAnywayBtn.disabled=false; signalerCancelBtn.disabled=false`) |
| Hardcoded test value / match-on-test-input | None — branch keys on the contract enum value `skipped_error`, not a fixture literal |
| Stub return / swallowed error | None — `catch (_)` block (app.js:306-310) is pre-existing, re-enables both buttons, shows recoverable copy (not swallowed) |
| Disabled/skipped tests | None |

## Button-state / stuck-panel audit
| State | Result |
|---|---|
| skipped_error branch button state | send-anyway stays DISABLED (set :266, never re-enabled on this branch), cancel re-enabled :300 — matches locked decision U2 |
| Panel dismissable after skipped_error | YES — cancel re-enabled → `signalerCancelBtn` click → `resetSignaler()` (:316) clears feedback, hides second row, re-enables submit. No stuck state |
| `signalerSecondRow.hidden` consistency | OK — set `true` at :271 before all success branches, so skipped_error hides the second row identically to the other branches |
| No silent no-op (AC2) | OK — every send-anyway outcome (404, generic !ok, created/incremented, skipped_error, unexpected, catch) calls `showSignalerFeedback` with visible copy |

## Security
- XSS: PASS. New branch calls `showSignalerFeedback("Le serveur n'a pas pu enregistrer le signalement. Réessayez plus tard.")` — a static string literal. `showSignalerFeedback` (app.js:128-131) renders via `textContent`, not `innerHTML`. No untrusted body field (`reason`/`verdict`/`ticket_id`) is interpolated on this branch. No injection surface.
- No secrets, no SQL, no SSRF, no JWT, no CORS surface in this frontend-only diff.
- Gateway untouched (pure passthrough confirmed: `build_passthrough` forwards raw bytes verbatim, workers_proxy.rs:71-78).

## Spec coverage
- AC-1 (skipped_error distinct from recoverable retry): met — app.js:293-300 distinct copy + distinct button state vs generic `else` :301-304. Behavior verified by reading the handler; NO automated JS regression test (see gap).
- AC-2 (no silent no-op): met — see button-state audit; every path yields visible feedback.
- AC-3 (cause logged worker-side): pre-existing, verified at ticket_service.py:60-66 (`reason="embed_failed"`) and :72-79 (`reason="db_failed"`), both `logger.error("ticket_service_failed", ...)` returning `action="skipped_error"`. No change required; not regressed.

## Test audit — `force_true_skipped_error_passthrough` (test_report_contradiction.rs:533-571)
- NOT a tautology. `build_passthrough` (workers_proxy.rs:71) is field-agnostic — forwards raw upstream bytes. The test exercises the full handler path (ownership check, forward, capped read, passthrough) and asserts `body["ticket_action"] == "skipped_error"` survives byte-for-byte. It proves the passthrough mechanism, not the frontend branch.
- Mock body shape matches real contract: `{"verdict":"absent","reason":...,"ticket_action":"skipped_error","ticket_id":null,"outcome":"indecisive"}` — `ticket_action` enum incl. `skipped_error` + `outcome` required, per `specs/openapi/gateway-to-workers.yml:337,365`. `ticket_id:null` matches `TicketResult(action="skipped_error", ticket_id=None, ...)` (ticket_service.py:66,79).
- Coverage scope: this test locks the gateway passthrough, NOT the frontend rendering logic (the actual #173 fix). The frontend branch has no CI regression lock.

## Green check (local)
- `cargo fmt --check`: PASS (exit 0).
- `cargo clippy --tests -- -D warnings`: PASS (exit 0).
- `cargo test --test test_report_contradiction --no-run`: PASS — compiles clean.
- `cargo test --test test_report_contradiction`: 11 passed, 14 failed. ALL 14 failures are `#[sqlx::test]` DB-backed with `Os code 11001 "Hôte inconnu."` (no local Postgres) — the documented ENV constraint affecting every DB test equally, incl. the new `force_true_skipped_error_passthrough`. NOT a code defect.

## Coverage gap (not a blocker)
- No automated JS/UI test harness for `app.js` — the actual #173 behavior fix (frontend branch) is verified by manual UI check only, per PRD #171. The Rust test only locks gateway passthrough, not the rendered copy/button-state. The fix cannot be regression-locked in CI today. Consistent with the plan's flagged gap and out-of-scope to build a harness here.

## Scope / first-step handler
- Confirmed the #173 impl commit did NOT touch the first-step submit handler. The first-step changes visible in `main...HEAD` (app.js:213-243, `showSecondStep` signature) belong to the unmerged #172 commit, not #173. The first-step `else` trap for `skipped_error` remains — correctly out of scope per locked decision #2 (separate follow-up). Not flagged as a #173 bug.

## Out-of-scope changes (in #173 impl commit only)
- None. The impl commit `45f1e28` touches only the 3 planned files. (The #172 worker/openapi/plan files in `main...HEAD` are a separate unmerged commit, not part of this review.)

## CHANGELOG accuracy
- Accurate. `## [Unreleased] > Fixed` entry states the verbatim approved copy, the disabled send-anyway / re-enabled cancel behavior, and the recoverable-vs-non-recoverable distinction. Matches the impl.
