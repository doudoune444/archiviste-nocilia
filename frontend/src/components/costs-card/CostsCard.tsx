/**
 * CostsCard — presentational component for GET /v1/costs data (#275).
 *
 * Pure: no gateway knowledge, no fetch calls. Receives a CostsResult
 * discriminated union and renders the three service lines (Postgres / GCS /
 * Workers) plus a total. Amounts are formatted in fr-FR euros (« 12,34 € »).
 * All server-returned values rendered as text — never dangerouslySetInnerHTML.
 */
import type { CostsResult } from "@/lib/observability-types";
import styles from "./CostsCard.module.css";

interface CostsCardProps {
  costs: CostsResult;
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

export function CostsCard({ costs }: CostsCardProps) {
  if (costs.kind === "error") {
    return (
      <article className={styles.card} aria-label="Coûts">
        <h2 className={styles.title}>Coûts</h2>
        <p className={styles.errorText}>Impossible de charger les coûts.</p>
        <p className={styles.requestId}>Requête&nbsp;: {costs.request_id}</p>
      </article>
    );
  }

  const services: Array<{ label: string; amount: number }> = [
    { label: "Postgres", amount: costs.services.postgres },
    { label: "GCS", amount: costs.services.gcs },
    { label: "Workers", amount: costs.services.workers },
  ];

  return (
    <article className={styles.card} aria-label="Coûts">
      <h2 className={styles.title}>Coûts</h2>
      <dl className={styles.dl}>
        {services.map((service) => (
          <div className={styles.line} key={service.label}>
            <dt className={styles.label}>{service.label}</dt>
            <dd className={styles.amount}>{formatEur(service.amount)}</dd>
          </div>
        ))}
        <div className={styles.totalLine}>
          <dt className={styles.totalLabel}>Total estimé</dt>
          <dd className={styles.totalAmount}>{formatEur(costs.total_eur)}</dd>
        </div>
      </dl>
    </article>
  );
}
