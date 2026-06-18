/**
 * StatsCard — presentational component for GET /v1/stats data.
 *
 * Pure: no gateway knowledge, no fetch calls.
 * Receives a StatsResult discriminated union and renders the appropriate state.
 * All server-returned strings rendered as text — never dangerouslySetInnerHTML.
 */
import type { StatsResult } from "@/lib/observability-types";
import styles from "./StatsCard.module.css";

interface StatsCardProps {
  stats: StatsResult;
}

export function StatsCard({ stats }: StatsCardProps) {
  if (stats.kind === "error") {
    return (
      <article className={styles.card} aria-label="Statistiques">
        <h2 className={styles.title}>Statistiques</h2>
        <p className={styles.errorText}>
          Impossible de charger les statistiques.
        </p>
        <p className={styles.requestId}>Requête&nbsp;: {stats.request_id}</p>
      </article>
    );
  }

  return (
    <article className={styles.card} aria-label="Statistiques">
      <h2 className={styles.title}>Statistiques</h2>
      <dl className={styles.dl}>
        <div className={styles.stat}>
          <dt className={styles.label}>Conversations</dt>
          <dd className={styles.value}>{stats.conversation_count}</dd>
        </div>
      </dl>
    </article>
  );
}
