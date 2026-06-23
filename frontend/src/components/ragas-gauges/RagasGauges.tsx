/**
 * RagasGauges — presentational component for GET /v1/quality data (issue #252).
 *
 * Pure: no gateway knowledge, no fetch calls. Server component (RSC) that
 * imports the InfoTooltip client leaf for the per-indicator and date tooltips.
 *
 * Renders four French-labelled score gauges (0..1) when metrics are present,
 * a clean empty state when no eval has run, or an error state with a request id.
 * The last-evaluation date is shown as a readable French day/month/year (no
 * time, Europe/Paris). The golden_set_version hash is intentionally not shown.
 * All server-returned strings rendered as text — never dangerouslySetInnerHTML.
 */
import type { QualityResult } from "@/lib/observability-types";
import { InfoTooltip } from "@/components/info-tooltip/InfoTooltip";
import styles from "./RagasGauges.module.css";

interface RagasGaugesProps {
  quality: QualityResult;
}

interface IndicatorDescriptor {
  label: string;
  technicalName: string;
  explanation: string;
  score: (metrics: QualityMetrics) => number;
}

type QualityMetrics = Extract<QualityResult, { kind: "ok" }>;

const INDICATORS: readonly IndicatorDescriptor[] = [
  {
    label: "Fidélité",
    technicalName: "faithfulness",
    explanation: "La réponse colle-t-elle aux sources récupérées, sans rien inventer ?",
    score: (metrics) => metrics.faithfulness,
  },
  {
    label: "Pertinence",
    technicalName: "answer relevancy",
    explanation: "La réponse répond-elle vraiment à la question posée ?",
    score: (metrics) => metrics.answer_relevancy,
  },
  {
    label: "Précision du contexte",
    technicalName: "context precision",
    explanation: "Les passages utiles sont-ils placés en tête des sources récupérées ?",
    score: (metrics) => metrics.context_precision,
  },
  {
    label: "Couverture du contexte",
    technicalName: "context recall",
    explanation: "A-t-on récupéré toutes les sources nécessaires pour répondre ?",
    score: (metrics) => metrics.context_recall,
  },
];

const DATE_EXPLANATION =
  "Date de la dernière évaluation automatique de la qualité du RAG.";

const dateFormatter = new Intl.DateTimeFormat("fr-FR", {
  day: "numeric",
  month: "long",
  year: "numeric",
  timeZone: "Europe/Paris",
});

function tooltipContent(descriptor: IndicatorDescriptor): string {
  return `${descriptor.label} (${descriptor.technicalName}) — ${descriptor.explanation}`;
}

function Gauge({ descriptor, value }: { descriptor: IndicatorDescriptor; value: number }) {
  const clamped = Math.min(1, Math.max(0, value));
  const percent = Math.round(clamped * 100);
  return (
    <div className={styles.gauge}>
      <span className={styles.gaugeLabel}>
        {descriptor.label}
        <InfoTooltip label={descriptor.label} content={tooltipContent(descriptor)} />
      </span>
      <div
        className={styles.gaugeBar}
        role="meter"
        aria-valuenow={value}
        aria-valuemin={0}
        aria-valuemax={1}
      >
        <div className={styles.gaugeFill} style={{ width: `${percent}%` }} />
      </div>
      <span className={styles.gaugeValue}>{value.toFixed(2)}</span>
    </div>
  );
}

function CardShell({ children }: { children: React.ReactNode }) {
  return (
    <article className={styles.card} aria-label="Qualité RAG">
      <h2 className={styles.title}>Qualité RAG</h2>
      {children}
    </article>
  );
}

export function RagasGauges({ quality }: RagasGaugesProps) {
  if (quality.kind === "error") {
    return (
      <CardShell>
        <p className={styles.errorText}>
          Impossible de charger les métriques de qualité.
        </p>
        <p className={styles.requestId}>Requête&nbsp;: {quality.request_id}</p>
      </CardShell>
    );
  }

  if (quality.kind === "no_data") {
    return (
      <CardShell>
        <p className={styles.emptyState}>
          Aucune évaluation disponible pour le moment.
        </p>
      </CardShell>
    );
  }

  return (
    <CardShell>
      <div className={styles.meta}>
        <time dateTime={quality.finished_at} className={styles.finishedAt}>
          {dateFormatter.format(new Date(quality.finished_at))}
        </time>
        <InfoTooltip label="À propos de la date d'évaluation" content={DATE_EXPLANATION} />
      </div>
      <div className={styles.gauges}>
        {INDICATORS.map((descriptor) => (
          <Gauge
            key={descriptor.technicalName}
            descriptor={descriptor}
            value={descriptor.score(quality)}
          />
        ))}
      </div>
    </CardShell>
  );
}
