# Plan — FIX-SIGNAL-173 distinct skipped_error on send-anyway

## Goal
On the "Envoyer quand même" (force=true) path, render the worker's `ticket_action="skipped_error"` (HTTP 200) as a DISTINCT, non-recoverable server-side failure message instead of the generic recoverable retry copy — so every send-anyway click yields a visible, accurate result and never a dead-end retry or silent no-op.

## Acceptance criteria recap
> NO `specs/acceptance/FIX-SIGNAL-173.md` exists — source is GitHub issue #173 (child 2 of PRD #171). Bullets below are the issue's intent, consistent with FIX-SIGNAL-172 (issue-as-spec), NOT a verbatim human-owned AC file (see U1):
- `skipped_error` rendered as a distinct server-side failure message, separate from the recoverable retry copy.
- No outcome of "Envoyer quand même" is a silent no-op — every click yields a visible accurate result.
- The failure cause is logged on the worker side.

## Human decisions already made (do not re-litigate)
1. **Copy (APPROVED):** distinct skipped_error message is exactly `"Le serveur n'a pas pu enregistrer le signalement. Réessayez plus tard."`
2. **Scope (APPROVED):** send-anyway handler ONLY. Do NOT touch the first-step submit handler — flag it as a follow-up ticket.
3. **Frontend-only.** No worker, no gateway-Rust, no OpenAPI, no migration change.
4. **Button state (APPROVED, U2):** after the skipped_error message, leave `signalerSendAnywayBtn` DISABLED (non-recoverable — do not reoffer a retry that cannot succeed) and re-enable `signalerCancelBtn` only. User can dismiss, not re-retry.

## Findings (established, do NOT re-investigate)
- Worker ALREADY emits `ticket_action="skipped_error"` AND logs the cause: `workers/src/archiviste_workers/services/ticket_service.py:60-66` (reason="embed_failed") and `:72-79` (reason="db_failed"), both `logger.error("ticket_service_failed", ...)`. → **AC3 already satisfied; no worker change.**
- `TicketAction` Literal already includes `skipped_error` (models.py:15, ticket_service.py:20). Contract already carries it.
- Gateway is a pure passthrough (`build_passthrough`) — NO Rust logic change.
- The send-anyway handler `signalerSendAnywayBtn` lives at app.js:251-303. Its success branch keys on `action === "created" || "incremented"` (line 286); the `else` at lines 293-296 buries `skipped_error` AND any unexpected action into the generic `"Impossible d'envoyer le signalement, réessayez."` with both buttons re-enabled (a retry the user can never resolve).
- `showSignalerFeedback` (app.js:128-131) is the existing feedback helper; button-state pattern (`signalerSendAnywayBtn.disabled` / `signalerCancelBtn.disabled`) is already used throughout the handler.

## Files to touch
- `gateway/static/assets/app.js` — in the `signalerSendAnywayBtn` success path, insert `else if (action === "skipped_error")` BEFORE the generic `else` (currently lines 293-296). Show the approved distinct copy via `showSignalerFeedback`; mirror the existing button-state handling (re-enable both buttons so the panel is not stuck, OR keep them disabled — see U2). Keep the trailing generic `else` for genuinely-unexpected action values. ~5-8 LOC.
- `CHANGELOG.md` — `## [Unreleased]` entry. ~2 LOC.

**HUMAN-OWNED, NOT touched by agent:**
- None. No `specs/`, no OpenAPI, no migration. Contract is unchanged (`skipped_error` already in the enum).

## Branch placement (the crux)
Current send-anyway success block (app.js:285-297):
```
const action = body && body.ticket_action;
if (action === "created" || action === "incremented") { ...success... }
else { ...generic retry... }          // <-- skipped_error wrongly lands here
```
Target:
```
if (action === "created" || action === "incremented") { ...success... }
else if (action === "skipped_error") { showSignalerFeedback(<approved copy>); signalerCancelBtn.disabled = false; }  // send-anyway stays disabled (U2)
else { ...generic retry, unexpected action only... }
```
The new branch must precede the generic `else` so `skipped_error` is caught explicitly; unknown actions still fall through to the recoverable generic copy.

## Test strategy
- **Manual UI check (primary, flagged gap):** there is NO automated UI/JS test harness for `app.js` — per PRD #171 frontend copy is verified manually. Verification = click "Envoyer quand même" against a worker stub returning `{"ticket_action":"skipped_error",...}` and confirm the distinct copy + non-stuck panel.
- **Rust passthrough (existing, cite):** `gateway/tests/test_report_contradiction.rs:101-149` (ac1_ac2) already asserts the workers 200 body survives gateway passthrough byte-for-byte (full-body assert + explicit `outcome` check). The passthrough is field-agnostic via `build_passthrough`, so `skipped_error` already survives. OPTIONAL ~8 LOC: add a force=true case asserting `body["ticket_action"] == "skipped_error"` survives passthrough, mirroring the existing `force_true_forwarded_to_workers` test (`:482-528`) — extend only; the existing test already proves the mechanism.
- **Property / contract / eval:** none. No contract change (enum already has `skipped_error`), no RAG path, no invariant in `specs/properties.md`.

## Implementation steps (ordered)
1. `gateway/static/assets/app.js` — add the `else if (action === "skipped_error")` branch in the send-anyway handler with the approved copy + button state (U2).
2. (Optional) `gateway/tests/test_report_contradiction.rs` — force=true `skipped_error` passthrough assertion, mirroring `force_true_forwarded_to_workers`.
3. `CHANGELOG.md` — `## [Unreleased]` entry.
4. Manual UI verification against a `skipped_error` worker stub.

## Risks / open questions
- **U1 (RESOLVED):** No `specs/acceptance/FIX-SIGNAL-173.md`. Human decision: GitHub issue #173 is the spec, consistent with FIX-SIGNAL-172 / FIX-CONVO-170. Do not author an acceptance file.
- **U2 (RESOLVED):** button state on the new branch. `skipped_error` is non-recoverable, so re-enabling "Envoyer quand même" would reintroduce an unresolvable retry — the exact bug #173 fixes. Human decision: leave `signalerSendAnywayBtn` DISABLED, re-enable `signalerCancelBtn` only.
- **No-JS-test gap (flagged):** `app.js` has no automated test harness; this slice is verified manually + the Rust passthrough test only. The behavior fix itself cannot be regression-locked in CI today. Out of scope to build a JS test harness here.
- **Behavior boundary:** the fix must NOT swallow genuinely-unexpected action values — those must keep the recoverable generic copy. The new branch is explicit-match only.

## Out of scope
- First-step submit handler (`signalerSubmitBtn`, app.js:229-243) has the SAME generic-else trap for `skipped_error` on the confirmed-but-write-failed branch (line 229-231). **Recommended follow-up ticket** — issue #173 is send-anyway only.
- Any worker change — `skipped_error` emission + ALERT logging already exist (ticket_service.py:60-79). AC3 is already met.
- Any gateway-Rust logic change — pure passthrough.
- OpenAPI / contract change — `skipped_error` already in the `TicketAction` enum.
- Retry/backoff logic, ticket-write self-heal, dedup, board changes.
- Building a JS unit-test harness for `app.js`.

## PRE-FLIGHT (human review before plan acceptance)

### (a) Files / dirs READ
- `gateway/static/assets/app.js` (lines 116-309: signaler helpers + both handlers)
- `gateway/tests/test_report_contradiction.rs` (grep: passthrough body, force tests, fn inventory)
- `specs/plans/FIX-SIGNAL-172.md` (format/style reference)
- (Cited from established findings, not re-read) `workers/src/archiviste_workers/services/ticket_service.py:60-79`, `models.py:15`

### (b) 3 KEY HYPOTHESES the plan rests on
1. **Frontend-only, ~5-8 LOC.** The entire fix is one `else if` branch in the send-anyway handler; worker, gateway-Rust, contract, and OpenAPI are all already correct.
2. **No new contract surface.** `skipped_error` is already in the `TicketAction` enum and already survives gateway passthrough (proved by test_report_contradiction.rs:101-149) — no schemathesis, no OpenAPI edit, no human-owned `specs/` touch.
3. **The generic `else` must remain** for unknown action values; the new branch is an explicit `skipped_error` match inserted before it, so unexpected actions still get the recoverable copy.

### (c) ZONES OF UNCERTAINTY (need human resolution)
- **U1** — no human-owned acceptance file; issue #173 is the spec (resolved, consistent with #172). Confirm.
- **U2 (RESOLVED)** — button state: cancel-only (send-anyway stays disabled, non-recoverable).
- **Test gap** — no automated JS test; manual verification + optional Rust passthrough assertion only. Confirm this is acceptable for ship.

> No human-owned `specs/` file is touched and no blocker per `.claude/rules/no-workaround.md`. Pending U2 + test-gap confirmation, this is the intended design.
