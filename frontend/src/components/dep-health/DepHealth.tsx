"use client";

/**
 * DepHealth — Client Component island for dependency health.
 *
 * WEBOBS-002: polls /api/v1/status (bff-proxy → gateway GET /v1/status) every
 * POLL_INTERVAL_MS and renders postgres/gcs/workers with an unambiguous
 * healthy/down status. Cleans up the interval on unmount.
 *
 * AC1: each dep renders healthy/down without ambiguity.
 * AC2: auto-refresh every ~60 s without manual reload.
 * AC3: polls through bff-proxy route /api/v1/status (same-origin).
 * AC4: fits the existing signal-card layout; responsive on mobile.
 */

import { useEffect, useReducer } from "react";
import {
  parseStatusBody,
  type DepHealthResult,
  type WorkersStatusValue,
} from "./parse-status";
import { InfoTooltip } from "@/components/info-tooltip/InfoTooltip";
import styles from "./DepHealth.module.css";

/** Explanation shown in the "En veille" info tooltip (#253 / #350, scale-to-zero). */
const DORMANT_EXPLANATION =
  "Les Workers tournent en scale-to-zero : ils s'éteignent au repos et redémarrent à froid à la demande.";

/** Verbatim scale-to-zero hint shown under the dormant Workers row (#350). */
const SCALE_TO_ZERO_HINT =
  "Workers en scale-to-zero : démarrage à froid à la demande.";

/** Named constant — no magic number (clean-code.md). Exported for test import. */
export const POLL_INTERVAL_MS = 60_000;

type State =
  | { phase: "loading" }
  | { phase: "ok"; result: DepHealthResult }
  | { phase: "error" };

type Action =
  | { type: "fetched"; result: DepHealthResult }
  | { type: "failed" };

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "fetched":
      return { phase: "ok", result: action.result };
    case "failed":
      return { phase: "error" };
    default:
      return state;
  }
}

async function fetchStatus(): Promise<DepHealthResult> {
  const response = await fetch("/api/v1/status");
  if (!response.ok) {
    return { kind: "error" };
  }
  const body: unknown = await response.json();
  return parseStatusBody(body);
}

interface DepRowProps {
  label: string;
  status: "ok" | "down";
}

function DepRow({ label, status }: DepRowProps) {
  const isHealthy = status === "ok";
  return (
    <div className={styles.depRow}>
      <span
        className={isHealthy ? styles.dotHealthy : styles.dotDown}
        aria-hidden="true"
      />
      <span className={styles.depLabel}>{label}</span>
      <span
        className={isHealthy ? styles.statusHealthy : styles.statusDown}
        aria-label={`${label} ${isHealthy ? "opérationnel" : "hors service"}`}
      >
        {isHealthy ? "Opérationnel" : "Hors service"}
      </span>
    </div>
  );
}

/**
 * Workers row — tri-state (#253). "dormant" renders "En veille" with a neutral
 * pill (never the red `down` style) plus an info tooltip explaining the cold start.
 * "ok"/"down" reuse the same visual contract as the binary rows.
 */
function WorkersRow({ status }: { status: WorkersStatusValue }) {
  if (status === "dormant") {
    return (
      <div className={styles.depRow}>
        <span className={styles.dotDormant} aria-hidden="true" />
        <span className={styles.depLabel}>Workers</span>
        <span className={styles.statusWrap}>
          <span className={styles.statusDormant} aria-label="Workers en veille">
            En veille
          </span>
          <InfoTooltip
            label="En veille : pourquoi ?"
            content={DORMANT_EXPLANATION}
          />
        </span>
      </div>
    );
  }
  return <DepRow label="Workers" status={status} />;
}

export function DepHealth() {
  const [state, dispatch] = useReducer(reducer, { phase: "loading" });

  useEffect(() => {
    let cancelled = false;

    async function poll(): Promise<void> {
      try {
        const result = await fetchStatus();
        if (!cancelled) {
          dispatch({ type: "fetched", result });
        }
      } catch {
        if (!cancelled) {
          dispatch({ type: "failed" });
        }
      }
    }

    void poll();
    const intervalId = setInterval(() => {
      void poll();
    }, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(intervalId);
    };
  }, []);

  if (state.phase === "loading") {
    return (
      <article className={styles.card} aria-label="Dépendances">
        <h2 className={styles.title}>Dépendances</h2>
        <p className={styles.loadingText}>Chargement…</p>
      </article>
    );
  }

  if (state.phase === "error" || state.result.kind === "error") {
    return (
      <article className={styles.card} aria-label="Dépendances">
        <h2 className={styles.title}>Dépendances</h2>
        <p className={styles.errorText}>
          Impossible de vérifier l&apos;état des dépendances.
        </p>
      </article>
    );
  }

  const { result } = state;
  const isWorkersDormant = result.workers === "dormant";

  return (
    <article className={styles.card} aria-label="Dépendances">
      <h2 className={styles.title}>Dépendances</h2>
      <div className={styles.body}>
        <div className={styles.deps}>
          <DepRow label="PostgreSQL" status={result.postgres} />
          <DepRow label="GCS" status={result.gcs} />
          <WorkersRow status={result.workers} />
        </div>
        {isWorkersDormant && <p className={styles.hint}>{SCALE_TO_ZERO_HINT}</p>}
      </div>
    </article>
  );
}
