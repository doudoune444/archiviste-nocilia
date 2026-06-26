/**
 * StatsCard — Conversations card for GET /v1/stats data (issue #350, PRD #346).
 *
 * Pure presentational: no gateway knowledge, no fetch calls. Receives a
 * StatsResult discriminated union and renders the appropriate state — a hero
 * count + "traitées au total" legend + an accessible info tooltip on success,
 * a leak-free request id on error.
 *
 * All server-returned values render as text — never dangerouslySetInnerHTML.
 */
import type { StatsResult } from "@/lib/observability-types";
import { InfoTooltip } from "@/components/info-tooltip/InfoTooltip";
import styles from "./StatsCard.module.css";

interface StatsCardProps {
  stats: StatsResult;
}

const COUNT_EXPLANATION =
  "Nombre total de conversations traitées par l'assistant.";

function CardShell({ children }: { children: React.ReactNode }) {
  return (
    <article className={styles.card} aria-label="Conversations">
      <h2 className={styles.title}>
        Conversations
        <InfoTooltip label="À propos des conversations" content={COUNT_EXPLANATION} />
      </h2>
      {children}
    </article>
  );
}

export function StatsCard({ stats }: StatsCardProps) {
  if (stats.kind === "error") {
    return (
      <CardShell>
        <p className={styles.errorText}>
          Impossible de charger les statistiques.
        </p>
        <p className={styles.requestId}>Requête&nbsp;: {stats.request_id}</p>
      </CardShell>
    );
  }

  return (
    <CardShell>
      <p className={styles.hero}>{stats.conversation_count}</p>
      <p className={styles.legend}>traitées au total</p>
    </CardShell>
  );
}
