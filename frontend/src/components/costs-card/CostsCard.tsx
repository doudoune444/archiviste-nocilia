/**
 * CostsCard — Coûts · 30 j card (#275, reworked for #349 / PRD #346).
 *
 * Pure presentational server component: no gateway knowledge, no fetch. Receives
 * a CostsResult discriminated union. Per the v03 mockup it leads with the period
 * total, then lists the three service lines — « Workers (LLM Mistral) »,
 * « PostgreSQL », « GCS » — each with a monospace amount and a bar whose width is
 * proportional to the total. A title InfoTooltip spells out the estimation
 * methodology, accessibly (hover + keyboard focus).
 *
 * Amounts are formatted in fr-FR euros (« 12,34 € »). All server-returned values
 * render as text — never dangerouslySetInnerHTML. On error the card shows a
 * request id only, leaking no internals (security.md).
 */
import type { CostsResult } from "@/lib/observability-types";
import { InfoTooltip } from "@/components/info-tooltip/InfoTooltip";
import styles from "./CostsCard.module.css";

const METHODOLOGY_LABEL = "Méthode d'estimation des coûts";
const METHODOLOGY_TEXT =
  "Estimation basée sur les tarifs publics GCP, hors crédits et remises.";

interface CostsCardProps {
  costs: CostsResult;
  /** CSP nonce; lets the proportional bar widths ship in a <style> the strict
   * style-src allows (inline style attributes are blocked in production). */
  nonce?: string;
}

interface ServiceLine {
  label: string;
  amount: number;
  barId: string;
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

function barWidth(amount: number, total: number): string {
  if (total <= 0) {
    return "0%";
  }
  const ratio = Math.min(1, Math.max(0, amount / total));
  return `${Math.round(ratio * 1000) / 10}%`;
}

function CardShell({ children }: { children: React.ReactNode }) {
  return (
    <article className={styles.card} aria-label="Coûts">
      {children}
    </article>
  );
}

function ServiceRow({ line, total }: { line: ServiceLine; total: number }) {
  return (
    <div className={styles.line}>
      <span className={styles.label}>{line.label}</span>
      <span className={styles.amount}>{formatEur(line.amount)}</span>
      <div
        className={styles.bar}
        role="meter"
        aria-label={line.label}
        aria-valuenow={line.amount}
        aria-valuemin={0}
        aria-valuemax={total}
      >
        <div id={line.barId} className={styles.barFill} />
      </div>
    </div>
  );
}

export function CostsCard({ costs, nonce }: CostsCardProps) {
  if (costs.kind === "error") {
    return (
      <CardShell>
        <h2 className={styles.title}>Coûts · 30 j</h2>
        <p className={styles.errorText}>Impossible de charger les coûts.</p>
        <p className={styles.requestId}>Requête&nbsp;: {costs.request_id}</p>
      </CardShell>
    );
  }

  const services: ServiceLine[] = [
    { label: "Workers (LLM Mistral)", amount: costs.services.workers, barId: "cost-bar-workers" },
    { label: "PostgreSQL", amount: costs.services.postgres, barId: "cost-bar-postgres" },
    { label: "GCS", amount: costs.services.gcs, barId: "cost-bar-gcs" },
  ];

  const barCss = services
    .map((line) => `#${line.barId}{width:${barWidth(line.amount, costs.total_eur)}}`)
    .join("");

  return (
    <CardShell>
      <style nonce={nonce}>{barCss}</style>
      <header className={styles.header}>
        <h2 className={styles.title}>Coûts · 30 j</h2>
        <InfoTooltip label={METHODOLOGY_LABEL} content={METHODOLOGY_TEXT} />
      </header>
      <p className={styles.total}>{formatEur(costs.total_eur)}</p>
      <div className={styles.lines}>
        {services.map((line) => (
          <ServiceRow key={line.label} line={line} total={costs.total_eur} />
        ))}
      </div>
    </CardShell>
  );
}
