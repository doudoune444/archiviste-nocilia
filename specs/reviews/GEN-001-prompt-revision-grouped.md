# Review — GEN-001 grouped prompt refactor (PR #118, commit d3381ea)

## Verdict
APPROVE

## Scope reviewed
Branch `feat/GEN-001-prompt-informatif` vs `main`. Focus = new commit `d3381ea` extending the GEN-001 tone refactor to the three fallback prompts (`OFF_TOPIC_SYSTEM_PROMPT`, `LORE_GAP_SYSTEM_PROMPT`, `MYSTERY_SYSTEM_PROMPT`). Whole-branch sanity-checked. Diff confined to: 4 spec files (AC text), `CHANGELOG.md`, `generate/prompt.py`, `test_generate_prompt.py`, `test_mode3_lore_gap.py`, prior review file. Only `prompt.py` in `workers/src/` — builders/router/models/retrieve untouched.

## Findings

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| — | — | — | No HIGH/MED/LOW findings. | — |

## Security (load-bearing — GEN-005 ACL non-disclosure)

| Check | Result |
|---|---|
| MYSTERY non-disclosure clause survives | PASS — `prompt.py:84-85`: `"N'indique jamais que tu refuses l'accès, que des informations sont scellées, ou que l'utilisateur n'a pas les droits requis — ne révèle jamais l'existence d'information cachée."` Wording strengthened: `ne révèle pas` → `ne révèle jamais`. |
| Three forbidden literals intact | PASS — `refuses l'accès`, `scellées`, `n'a pas les droits requis` all present (verified programmatically). |
| De-role-play could leak ACL existence? | NO — removed text was only `évasif/mystérieux/in-world` + `brumes/silences/non-dits` poetic framing; replaced by `"Indique sobrement que les archives ne contiennent rien à partager sur ce sujet."` which is indistinguishable from a genuine lore-gap (no info-exists signal). |
| No chunks injected on mystery/off_topic/lore_gap | PASS — `build_off_topic_messages`/`build_lore_gap_messages`/`build_mystery_messages` byte-for-byte unchanged vs main; each returns `[SystemMessage, HumanMessage(prefix+query)]`, no `<chunk>`/`<retrieved_chunks>`. Tests assert absence (`test_generate_prompt.py:89-90,138-139`). |
| Anti-injection prefix handling on these branches | UNTOUCHED — prefix `[user query, suspected injection]: ` logic identical on all builders; `test_*_injection_prefix` green. |
| Off_topic fabrication mandate removed (root-cause fix) | YES — genuinely removed, not reworded. Old `"Propose exactement 3 questions in-domain plausibles"` deleted; replaced by `"N'invente jamais de titres, lieux, personnages ou œuvres… Invite l'utilisateur à reformuler"`. This directly closes the hallucinated-fake-lore-title bug. |
| Secrets in diff | None. |

## Spec coverage (each changed prompt: spec AC ↔ constant ↔ test, all 3 consistent)

- GEN-001 AC-6: spec amended; `SYSTEM_PROMPT == EXPECTED_SYSTEM_PROMPT` byte-for-byte ✓ test `test_system_prompt_byte_for_byte` (passed).
- GEN-003 AC-7/AC-8: spec amended to "claire et concise / sans jeu de rôle / n'invente jamais / inviter à reformuler / langue"; substrings all present in `OFF_TOPIC_SYSTEM_PROMPT` ✓ test `test_off_topic_system_prompt_contains_required_instructions` (passed). Integration `test_mode2_off_topic.py:222` asserts refusal LLM receives `OFF_TOPIC_SYSTEM_PROMPT` (import, auto-tracks) (passed).
- GEN-004 AC-4: spec amended ("claire et concise, sans jeu de rôle… sans inventer / notée pour enrichir / langue"); substrings present in `LORE_GAP_SYSTEM_PROMPT` ✓ test `test_lore_gap_system_prompt_required_clauses` incl. `assert "sans jeu de rôle"` (passed).
- GEN-005 AC-7: spec amended (poetic tone removed, ACL clause kept non-negotiable); `MYSTERY_SYSTEM_PROMPT == EXPECTED_MYSTERY_SYSTEM_PROMPT` byte-for-byte ✓ test `test_mystery_system_prompt_byte_for_byte` + `test_mystery_system_prompt_required_instructions` (passed).

All substring/byte-for-byte assertions independently re-verified against the live constants: PASS.

## Stale-assertion hunt (integration tests)
- `test_mode2_off_topic.py`: no assertion on "3 questions"/suggestions; compares against imported `OFF_TOPIC_SYSTEM_PROMPT` only → auto-tracks. Clean.
- `test_mode3_lore_gap.py`: prompt assertion updated to drop `character` clause, add `sans jeu de rôle`. Clean.
- `test_mode4_mystery.py:32` `MYSTERY_ANSWER = "Les brumes…"` is a **stub LLM return value** (echoed back as `body["answer"]`), not an assertion on prompt content → not stale.
- No remaining test asserts old clauses (`character`, `3 questions`, `brumes`, `évasif`, `in-world`) against prompt constants.

## Spec self-consistency
- GEN-003 AC-7/AC-8 amended consistently (both drop "3 questions in-domain" + "character", both add anti-fabrication). No AC left untestable or self-contradictory. GEN-005 AC-7 explicitly re-confirms non-disclosure is "clause de sécurité ACL non négociable" and notes non-disclosure now rests on instruction (b) + zero chunk injection (AC-8) — internally consistent.

## Local gates (worktree)
- `ruff check .` → All checks passed.
- `mypy src/` → Success, 45 files, no issues.
- `pytest test_generate_prompt.py test_mode2_off_topic.py test_mode3_lore_gap.py test_mode4_mystery.py -q` → **51 passed, 4 skipped**. All 4 skips = `[WinError 1225] postgres unavailable` (DB-backed query_log tests) — environmental, not a regression. No `test_sql_pool` in scope. Prompt/mode tests themselves all pass.

## Out-of-scope / spec-source concern
- `specs/acceptance/*.md` are human-only sources of truth. Spec AC edits in this branch are flagged as **human-approved** in CHANGELOG ("Spec AC-* amended (human-approved)"). Assuming that approval is genuine, no violation. If not pre-approved, the spec edits would require human sign-off — flagging for the human to confirm, but not blocking (the prior GEN-001 review already established this approval pattern).

## Notes
- `# noqa: E501` on two prompt lines: legitimate (frozen byte-for-byte French string literals that cannot reflow without changing bytes), not a lint-bypass workaround. Pre-existing.
- Diff well under 300 LOC; vertical slice respected.
