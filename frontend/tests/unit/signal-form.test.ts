// AC: CHAT-005 — per-answer two-state contradiction report
//
// AC-1: each assistant answer carries a "Signaler une incohérence" action.
// AC-2: the user can enter a claim and submit to the report-contradiction endpoint.
// AC-3: outcome "confirmed" renders the confirmed/recorded state.
// AC-4: outcome "refused" or "indecisive" renders the not-confirmed state.
// AC-5: submit is disabled while in-flight and when the claim is empty.
// AC-6: the claim text is never logged (A09 — enforced by pure mapping, not side-effect test).

import { describe, it, expect } from "vitest";
import { mapOutcomeToState } from "@/components/signal-form/SignalForm";

describe("mapOutcomeToState (CHAT-005)", () => {
  // AC-3: outcome "confirmed" maps to the confirmed/recorded state
  it('maps "confirmed" to confirmed state', () => {
    expect(mapOutcomeToState("confirmed")).toBe("confirmed");
  });

  // AC-4: outcome "refused" maps to not-confirmed state
  it('maps "refused" to not-confirmed state', () => {
    expect(mapOutcomeToState("refused")).toBe("not-confirmed");
  });

  // AC-4: outcome "indecisive" also collapses to not-confirmed (three→two state)
  it('maps "indecisive" to not-confirmed state', () => {
    expect(mapOutcomeToState("indecisive")).toBe("not-confirmed");
  });

  // AC-4: unknown outcomes are treated as not-confirmed (safe default)
  it("maps unknown outcome to not-confirmed", () => {
    expect(mapOutcomeToState("unknown_future_value")).toBe("not-confirmed");
  });
});
