"use client";
/**
 * SignalForm — per-answer contradiction report control (CHAT-005).
 *
 * Renders a "Signaler une incohérence" toggle under each committed assistant
 * answer. On click, reveals a claim textarea and a submit button. On response,
 * collapses to a two-state outcome: confirmed (recorded) or not-confirmed.
 *
 * AC-1: each assistant answer carries a "Signaler une incohérence" action.
 * AC-2: user can enter a claim and submit to POST /api/v1/report-contradiction.
 * AC-3: outcome "confirmed" → confirmed/recorded state.
 * AC-4: "refused" | "indecisive" → not-confirmed state (three→two mapping).
 * AC-5: submit disabled while in-flight and when claim is empty.
 *
 * A09: the claim text is never logged — fetch error is caught and discarded
 *      (only the boolean outcome is surfaced to the user).
 * A03: outcome and reason rendered as plain text — never dangerouslySetInnerHTML.
 * A01: the cookie is forwarded by the BFF route — user identity NOT sent in body.
 */

import { useState, useCallback } from "react";
import styles from "./signal-form.module.css";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Two-state outcome after collapsing the three gateway verdict values. */
export type SignalOutcome = "confirmed" | "not-confirmed";

/** Shape of the JSON body accepted by POST /api/v1/report-contradiction. */
interface ReportBody {
  claim: string;
  conversation_id: string;
  citations?: unknown[];
}

/** Minimal shape we read from the gateway passthrough response. */
interface GatewayResponse {
  outcome?: string;
}

// ---------------------------------------------------------------------------
// Pure mapping — exported so the unit test can import it without React
// ---------------------------------------------------------------------------

/**
 * Maps the three-value gateway outcome to the two-state UI verdict.
 *
 * "confirmed" → confirmed (claim recorded, ticket may be created).
 * "refused" | "indecisive" | anything else → not-confirmed.
 * This collapse is intentional (FIX-SIGNAL deferred per CHAT-005 spec).
 */
export function mapOutcomeToState(outcome: string): SignalOutcome {
  return outcome === "confirmed" ? "confirmed" : "not-confirmed";
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const REPORT_PATH = "/api/v1/report-contradiction";

/** French label for the trigger button. */
const TRIGGER_LABEL = "Signaler une incohérence";

interface SignalFormProps {
  conversationId: string;
  citations?: unknown[];
}

/** Internal UI phases for the signal control. */
type Phase = "idle" | "open" | "submitting" | "done";

export function SignalForm({
  conversationId,
  citations,
}: SignalFormProps): React.ReactElement {
  const [phase, setPhase] = useState<Phase>("idle");
  const [claim, setClaim] = useState("");
  const [outcome, setOutcome] = useState<SignalOutcome | null>(null);

  const handleOpen = useCallback(() => {
    setPhase("open");
  }, []);

  const handleSubmit = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const trimmedClaim = claim.trim();
      if (!trimmedClaim || phase === "submitting") return;

      setPhase("submitting");

      const body: ReportBody = {
        claim: trimmedClaim,
        conversation_id: conversationId,
      };
      if (citations !== undefined && citations.length > 0) {
        body.citations = citations;
      }

      try {
        const response = await fetch(REPORT_PATH, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          // A09: claim is in the body, not logged.
          body: JSON.stringify(body),
        });

        if (!response.ok) {
          setOutcome("not-confirmed");
          setPhase("done");
          return;
        }

        const json = (await response.json()) as GatewayResponse;
        const rawOutcome = typeof json.outcome === "string" ? json.outcome : "";
        setOutcome(mapOutcomeToState(rawOutcome));
      } catch {
        // A09: network error message may contain context — never log.
        setOutcome("not-confirmed");
      }

      setPhase("done");
    },
    [claim, conversationId, citations, phase]
  );

  if (phase === "idle") {
    return (
      <div className={styles.wrapper}>
        <button
          type="button"
          className={styles.triggerButton}
          onClick={handleOpen}
        >
          {TRIGGER_LABEL}
        </button>
      </div>
    );
  }

  if (phase === "done" && outcome !== null) {
    return (
      <div className={styles.wrapper}>
        {outcome === "confirmed" ? (
          <p
            className={styles.outcomeConfirmed}
            data-testid="signal-outcome-confirmed"
          >
            Signalement enregistré. Merci pour votre contribution.
          </p>
        ) : (
          <p
            className={styles.outcomeNotConfirmed}
            data-testid="signal-outcome-not-confirmed"
          >
            Signalement pris en compte, mais l&apos;incohérence n&apos;a pas pu
            être confirmée.
          </p>
        )}
      </div>
    );
  }

  // phase === "open" | "submitting"
  const isSubmitting = phase === "submitting";
  const isDisabled = isSubmitting || claim.trim() === "";

  return (
    <div className={styles.wrapper}>
      <form className={styles.form} onSubmit={handleSubmit}>
        <label htmlFor="signal-claim" className={styles.claimLabel}>
          {TRIGGER_LABEL}
        </label>
        <textarea
          id="signal-claim"
          className={styles.claimTextarea}
          value={claim}
          onChange={(e) => setClaim(e.target.value)}
          disabled={isSubmitting}
          rows={3}
          placeholder="Décrivez l'incohérence que vous avez constatée…"
          aria-label="Description de l'incohérence"
        />
        <button
          type="submit"
          className={styles.submitButton}
          disabled={isDisabled}
          aria-busy={isSubmitting}
        >
          {isSubmitting ? "Envoi en cours…" : "Envoyer le signalement"}
        </button>
      </form>
    </div>
  );
}
