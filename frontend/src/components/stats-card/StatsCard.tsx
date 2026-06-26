/**
 * StatsCard — Conversations card for GET /v1/stats data (#350).
 *
 * Pure: no gateway knowledge, no fetch calls. Receives a StatsResult
 * discriminated union and renders the appropriate state. Reshaped from the v03
 * mockup: a band-label header with an accessible info tooltip, a centred hero
 * number, and the « traitées au total » legend.
 *
 * All server-returned strings rendered as text — never dangerouslySetInnerHTML.
 */
import type { StatsResult } from "@/lib/observability-types";
import { InfoTooltip } from "@/components/info-tooltip/InfoTooltip";
import styles from "./StatsCard.module.css";

interface StatsCardProps {
  stats: StatsResult;
}

const COUNT_EXPLANATION =
  "Nombre total de conversations traitées par l'assistant.";

export function StatsCard({ stats }: StatsCardProps) {
  if (stats.kind === "error") {
    return (
      <article className={styles.card} aria-label="Conversations">
        <Header />
        <p className={styles.errorText}>
          Impossible de charger les statistiques.
        </p>
        <p className={styles.requestId}>Requête&nbsp;: {stats.request_id}</p>
      </article>
    );
  }

  return (
    <article className={styles.card} aria-label="Conversations">
      <Header />
      <div className={styles.body}>
        <p className={styles.hero}>{stats.conversation_count}</p>
        <p className={styles.caption}>traitées au total</p>
      </div>
    </article>
  );
}

function Header() {
  return (
    <div className={styles.bandLabel}>
      <span className={styles.dot} aria-hidden="true" />
      <span>Conversations</span>
      <InfoTooltip label="En savoir plus" content={COUNT_EXPLANATION} />
    </div>
  );
}
