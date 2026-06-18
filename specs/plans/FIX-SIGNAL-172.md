# Plan — FIX-SIGNAL-172 typed signal outcome + robust verdict parse

## Goal
Surface the signal panel's result as an explicit typed `outcome` (`refused`/`confirmed`/`indecisive`) on the worker response — derived from the judge majority — and fix the `parse_verdict` ASCII-only regex that mis-classifies accented-lead replies, so the frontend renders one truthful message per outcome and no spurious tickets are raised.

## Acceptance criteria recap
> NO `specs/acceptance/FIX-SIGNAL-172.md` exists — source is GitHub issue #172 (parent PRD #171). Bullets below are the issue's intent, NOT a verbatim human-owned AC file (see U1):
- Worker maps judge majority to outcome: `present` majority → `refused` (lore-consistent, no ticket); `absent`/`contradiction` majority → `confirmed` (+ ticket); `unclear`/no-majority → `indecisive` (no ticket).
- Worker response carries an explicit `outcome` field alongside `verdict` + `reason` + `ticket_action`. Gateway passes through unchanged (`build_passthrough`, no Rust change).
- OpenAPI `VerifyContradictionResponse` gains `outcome` enum.
- Frontend renders one distinct message per outcome + human-readable reason; `refused`/`indecisive` offer "Envoyer quand même"/"Annuler"; `confirmed` shows "Signalement enregistré".
- Tests: worker asserts the three classifications; contract pins the outcome enum.

## Human decisions already made (do not re-litigate)
1. `unclear`/no-majority → `indecisive`, NO ticket. Removes `unclear` from `TICKET_TRIGGERING_VERDICTS` (currently `{absent, contradiction, unclear}`, models.py:21). Intended behavior change.
2. `outcome` is an explicit worker field, not frontend-derived.

## Files to touch
- `workers/src/archiviste_workers/contradiction/models.py` — add `Outcome = Literal["refused","confirmed","indecisive"]`; add `outcome: Outcome` field to `VerifyContradictionResponse`; remove `"unclear"` from `TICKET_TRIGGERING_VERDICTS` (→ `{absent, contradiction}`). ~6 LOC.
- `workers/src/archiviste_workers/contradiction/prompt.py` — replace `_LEADING_TOKEN_RE` scan with first-known-keyword scan robust to a leading accented/non-ASCII word; keep fail-safe→unclear + reason=remainder. ~15 LOC.
- `workers/src/archiviste_workers/contradiction/service.py` — add `outcome: Outcome` to `VerificationResult`; derive outcome at each return site of `verify_contradiction` (no-sources, should_raise, force, no-ticket). ~20 LOC.
- `workers/src/archiviste_workers/contradiction/router.py` — pass `outcome=result.outcome` into `VerifyContradictionResponse`; add `outcome` to the log line. ~3 LOC.
- `gateway/static/assets/app.js` — branch submit + send-anyway handlers on `body.outcome` instead of only `ticket_action`; distinct copy for refused vs indecisive vs confirmed; map raw verdict token to human copy. ~45 LOC.
- `workers/tests/test_contradiction.py` — outcome classification tests + new parse_verdict cases; FIX the existing `unclear >=2 → ticket` cases (lines 204-205, 243-256) now that unclear no longer triggers. ~50 LOC.
- `gateway/tests/test_report_contradiction.rs` — contract/passthrough test asserts `outcome` survives passthrough (extend AC-2 body at line 113/143). ~10 LOC.
- `CHANGELOG.md` — `## [Unreleased]` entry. ~3 LOC.

**HUMAN-OWNED, NOT touched by agent (flag for explicit sign-off, do not pre-write):**
- `specs/openapi/gateway-to-workers.yml` — add `outcome` enum to `VerifyContradictionResponse` (~line 335-362), add to `required`, update `ticket_action`/verdict descriptions (unclear no longer triggers).
- `specs/acceptance/FIX-SIGNAL-172.md` — author the AC file (U1).
- `specs/properties.md` — only if human wants outcome↔ticket as an invariant (do not add).

## Outcome derivation (the crux)
| panel result | verdict | has_majority | ticket | `outcome` |
|---|---|---|---|---|
| present majority | present | true | none | `refused` |
| absent/contradiction majority | absent/contradiction | true | created/incremented | `confirmed` |
| unclear majority OR no majority | unclear | either | none | `indecisive` |
| no sources | unclear | — | none | `indecisive` |
| `force=True` after non-confirm | present | — | created (judges_not_passed) | `refused` |
| `force=True` after non-confirm | unclear | — | created (judges_not_passed) | `indecisive` |

Derivation rule (RESOLVED, U3): `outcome` is a pure function of the judge result, orthogonal to ticketing — `confirmed` iff `should_raise` (judge-confirmed `absent`/`contradiction` majority); `refused` iff `verdict == present`; else `indecisive`. A forced ticket NEVER reports `confirmed` — by design a force-raised ticket is the untrusted/`judges_not_passed` path and keeps its underlying `refused`/`indecisive` outcome (human decision: anyone can force, so a forced ticket must not masquerade as judge-confirmed).

## Frontend copy (APPROVED, U4)
Per-outcome message (followed by the human-readable `reason`). Raw verdict token is NEVER shown to the user.
- `confirmed` → `"Incohérence confirmée — signalement enregistré."` — no second row.
- `refused` → `"Le lore est cohérent, signal refusé."` — offer "Envoyer quand même" / "Annuler".
- `indecisive` → `"Les juges n'ont pas pu trancher."` — offer "Envoyer quand même" / "Annuler".
- send-anyway success (force) keeps existing `"Signalement envoyé malgré l'absence de confirmation par les juges."`
Frontend branches on `body.outcome` (not `ticket_action`) for the first-step message. The send-anyway handler still keys off `ticket_action` (created/incremented) for success.

## Test strategy
- Worker classification (3): `present×2 → outcome refused, not_raised`; `absent×2 → outcome confirmed, ticket created`; `contradiction×2 → confirmed`; `unclear×2 → indecisive, NO ticket` (replaces old "unclear→ticket"); split-vote → `indecisive`. Oracle = `VerificationResult.outcome` + `ticket_action`.
- parse_verdict unit (add to parametrize at prompt.py test, line 82-106): accented lead `"Évaluation: PRESENT ..."` → `present` + clean reason; lowercase `"present ..."` → `present`; garbage `"Je ne sais pas"` → `unclear`; empty `""` → `unclear`; keyword-mid-sentence still found.
- Contract/passthrough: `gateway/tests/test_report_contradiction.rs` AC-2 body includes `"outcome":"confirmed"`; assert `body["outcome"]` survives byte-for-byte (passthrough, no Rust logic).
- Schemathesis: runs only after the human edits the OpenAPI — flag as gated step, not agent-driven.
- Eval: not touched (no RAG generation-path change).

## Implementation steps (ordered)
1. **GATE**: human authors `specs/acceptance/FIX-SIGNAL-172.md` (U1) and signs off the OpenAPI `outcome` edit. Do not merge before.
2. `prompt.py` parse_verdict fix — TDD: add failing accented-lead + lowercase cases first.
3. `models.py` — `Outcome` Literal + field; shrink `TICKET_TRIGGERING_VERDICTS`.
4. `service.py` — `outcome` on `VerificationResult` + derive at all return sites.
5. `router.py` — pass `outcome` through + log field.
6. `workers/tests/test_contradiction.py` — classification tests; repair the unclear-triggers-ticket cases.
7. Human edits `specs/openapi/gateway-to-workers.yml` (gated); then `gateway/tests/test_report_contradiction.rs` passthrough assert; run schemathesis.
8. `gateway/static/assets/app.js` — outcome branching + per-verdict human copy.
9. CHANGELOG entry.

## Risks / open questions
- **U1 (RESOLVED):** No acceptance file. Human decision: skip it — GitHub issue #172 is the spec, consistent with FIX-CONVO-170. No `specs/acceptance/FIX-SIGNAL-172.md` authored.
- **U2 (BLOCKER, contract):** `outcome` on `VerifyContradictionResponse` is a human-owned `specs/openapi/` edit — explicit sign-off required. Contract test + schemathesis are gated on it.
- **U3 (RESOLVED):** Force path reports the UNDERLYING outcome (`refused`/`indecisive`), never `confirmed`. A forced ticket is the untrusted `judges_not_passed` path and must not masquerade as judge-confirmed. `outcome` stays a 3-value enum, orthogonal to ticketing.
- **Behavior change (intended, decision #1):** removing `unclear` from `TICKET_TRIGGERING_VERDICTS` means `unclear×2` no longer raises a ticket. Existing tests at test_contradiction.py:204-205 and :243-256 encode the OLD behavior and MUST be updated, not deleted-to-pass (per CLAUDE.md "jamais désactiver un test").
- **Frontend (no spec):** `app.js` is not behind an AC; copy strings are author-owned content. Outcome→message mapping (esp. verdict-token→French copy) needs human-approved wording. List proposed strings for review before implementing.

## Out of scope
- Any Rust handler logic change — gateway is already a pure passthrough (`build_passthrough`, confirmed in chat.rs:232 / report_contradiction.rs:382). Only a test-data extension.
- A 4th `forced` outcome enum value (unless U3 resolves that way).
- Changing judge prompt wording, lens count, majority threshold, or redaction logic.
- Dedup / ticket-board changes (#163/#175 territory).
- New property invariant in `specs/properties.md`.
- Generation/RAG eval changes.

## PRE-FLIGHT (human review before plan acceptance)

### (a) Files / dirs READ
- `workers/src/archiviste_workers/contradiction/models.py`
- `workers/src/archiviste_workers/contradiction/prompt.py`
- `workers/src/archiviste_workers/contradiction/service.py`
- `workers/src/archiviste_workers/contradiction/router.py`
- `specs/openapi/gateway-to-workers.yml` (lines 320-379, VerifyContradictionResponse)
- `gateway/static/assets/app.js` (lines 120-319, signal handlers)
- `gateway/src/handlers/workers_proxy.rs` + `chat.rs` + `report_contradiction.rs` (passthrough confirmation, grep)
- `gateway/tests/test_report_contradiction.rs` (header + assertions grep)
- `workers/tests/test_contradiction.py` (test inventory grep)
- `specs/plans/FIX-CONVO-170.md` (style reference)

### (b) 3 KEY HYPOTHESES the plan rests on
1. **Gateway needs zero Rust logic change.** Both contradiction-path handlers byte-for-byte passthrough the workers 200 body (`build_passthrough`); a new `outcome` field flows through automatically. Only test-data + the human-owned OpenAPI need touching.
2. **`outcome` is fully derivable from existing `verify_contradiction` state** (`verdict`, `has_majority`, `should_raise`) — no new judge call, no aggregation change. Rule: ticket-raised→`confirmed`, present→`refused`, else→`indecisive`.
3. **The parse bug and the outcome feature are one vertical slice** (~150 LOC excl. the human OpenAPI/AC). The parse fix is the root cause of the prod spurious-ticket symptom; shipping outcome without it would still mis-classify accented replies. Combined diff stays ≤300 LOC.

### (c) ZONES OF UNCERTAINTY (need human resolution)
- **U1** — no human-owned acceptance file; must be authored before code (blocker).
- **U2** — `outcome` enum is a human-owned `specs/openapi/` edit; contract test + schemathesis gated on sign-off (blocker).
- **U3** — `outcome` value on the `force=True` branch: report `confirmed`, or introduce a 4th `forced` enum value (out of current scope)? Need a decision.
- **U4** — `app.js` is not behind an AC; the exact French copy per outcome (and the verdict-token→human mapping) is author-owned content needing approved wording before implementing.

> **STOP POINT:** U1 + U2 require authoring/editing human-owned `specs/`. Per `.claude/rules/no-workaround.md`, no code is proposed that silently overrides them. The plan above is the intended design pending sign-off.
