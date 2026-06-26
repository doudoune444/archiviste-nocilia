/**
 * CostsCard — presentational component for GET /v1/costs data (#275, #349).
 *
 * Pure: no gateway knowledge, no fetch calls. Receives a CostsResult
 * discriminated union. Per the v03 mockup it leads with the rolling-period
 * total, then lists three service lines — « Workers (LLM Mistral) »,
 * « PostgreSQL », « GCS » — each with a monospace amount (fr-FR euros) and a
 * progress bar whose width is proportional to the total.
 *
 * All server-returned values rendered as text — never dangerouslySetInnerHTML.
 * An InfoTooltip on the title spells out the estimation methodology, accessibly
 * (hover / keyboard focus / screen reader). Error state shows only a request id.
 */
import type { CostsResult } from "@/lib/observability-types";
import { InfoTooltip } from "@/components/info-tooltip/InfoTooltip";
import styles from "./CostsCard.module.css";

const METHODOLOGY_LABEL = "Méthode d'estimation des coûts";
const METHODOLOGY_TEXT =
  "Estimation basée sur les tarifs publics GCP, hors crédits et remises.";

interface CostsCardProps {
  costs: CostsResult;
}

interface ServiceLine {
  label: string;
  amount: number;
}

const euroFormatter = new Intl.NumberFormat("fr-FR", {
  style: "currency",
  currency: "EUR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function formatEur(amount: number): string {
  return euroFormatter.format(amount);
}

/** Bar fill as a percentage of the period total; clamped to [0, 100]. */
function fillPercent(amount: number, total: number): number {
  if (total <= 0) return 0;
  return Math.min(100, Math.max(0, (amount / total) * 100));
}

export function CostsCard({ costs }: CostsCardProps) {
  if (costs.kind === "error") {
    return (
      <article className={styles.card} aria-label="Coûts">
        <h2 className={styles.title}>Coûts · 30 j</h2>
        <p className={styles.errorText}>Impossible de charger les coûts.</p>
        <p className={styles.requestId}>Requête&nbsp;: {costs.request_id}</p>
      </article>
    );
  }

  const services: ServiceLine[] = [
    { label: "Workers (LLM Mistral)", amount: costs.services.workers },
    { label: "PostgreSQL", amount: costs.services.postgres },
    { label: "GCS", amount: costs.services.gcs },
  ];

  return (
    <article className={styles.card} aria-label="Coûts">
      <header className={styles.header}>
        <h2 className={styles.title}>Coûts · 30 j</h2>
        <InfoTooltip label={METHODOLOGY_LABEL} content={METHODOLOGY_TEXT} />
      </header>

      <div className={styles.total}>
        <span className={styles.totalAmount}>{formatEur(costs.total_eur)}</span>
        <span className={styles.totalLabel}>total période</span>
      </div>

      <dl className={styles.lines}>
        {services.map((service) => (
          <div className={styles.line} key={service.label}>
            <dt className={styles.label}>{service.label}</dt>
            <dd className={styles.amount}>{formatEur(service.amount)}</dd>
            <div
              className={styles.bar}
              role="progressbar"
              aria-label={`Part de ${service.label} dans le total`}
              aria-valuemin={0}
              aria-valuemax={costs.total_eur}
              aria-valuenow={service.amount}
            >
              <span
                className={styles.barFill}
                style={{ width: `${fillPercent(service.amount, costs.total_eur)}%` }}
              />
            </div>
          </div>
        ))}
      </dl>
    </article>
  );
}
