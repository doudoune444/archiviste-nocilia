/**
 * RagasGauges — presentational component for GET /v1/quality data.
 *
 * Pure: no gateway knowledge, no fetch calls.
 * Renders four labeled score gauges (0..1) when metrics are present,
 * a clean empty state when no eval has run, or an error state with a request id.
 * All server-returned strings rendered as text — never dangerouslySetInnerHTML.
 *
 * Issue #252: French labels + per-indicator info tooltips (slice-1 component),
 * a readable French last-evaluation date (no time), and the golden-set version
 * hash removed from the display. The hash is still returned by the API but no
 * longer rendered.
 */
import type { QualityResult } from "@/lib/observability-types";
import { InfoTooltip } from "@/components/info-tooltip/InfoTooltip";
import styles from "./RagasGauges.module.css";

interface RagasGaugesProps {
  quality: QualityResult;
}

interface Indicator {
  label: string;
  technicalName: string;
  explanation: string;
  value: number;
}

interface GaugeProps {
  indicator: Indicator;
}

const DATE_TOOLTIP_LABEL = "Date de la dernière évaluation";
const DATE_TOOLTIP_CONTENT =
  "Date de la dernière évaluation automatique de la qualité du RAG.";

const dateFormatter = new Intl.DateTimeFormat("fr-FR", {
  day: "numeric",
  month: "long",
  year: "numeric",
  timeZone: "Europe/Paris",
});

function formatLastEvaluationDate(finishedAt: string): string {
  return dateFormatter.format(new Date(finishedAt));
}

function indicatorTooltip({ label, technicalName, explanation }: Indicator) {
  return {
    triggerLabel: `${label} (${technicalName})`,
    content: `${label} (${technicalName}) — « ${explanation} »`,
  };
}

function Gauge({ indicator }: GaugeProps) {
  // Clamp to [0, 1] so a score outside the valid range never produces a
  // negative width or a bar wider than the container.
  const clamped = Math.min(1, Math.max(0, indicator.value));
  const percent = Math.round(clamped * 100);
  const tooltip = indicatorTooltip(indicator);
  return (
    <div className={styles.gauge}>
      <span className={styles.gaugeLabel}>
        {indicator.label}
        <InfoTooltip label={tooltip.triggerLabel} content={tooltip.content} />
      </span>
      <div
        className={styles.gaugeBar}
        role="meter"
        aria-valuenow={indicator.value}
        aria-valuemin={0}
        aria-valuemax={1}
      >
        <div className={styles.gaugeFill} style={{ width: `${percent}%` }} />
      </div>
      <span className={styles.gaugeValue}>{indicator.value.toFixed(2)}</span>
    </div>
  );
}

export function RagasGauges({ quality }: RagasGaugesProps) {
  if (quality.kind === "error") {
    return (
      <article className={styles.card} aria-label="Qualité RAG">
        <h2 className={styles.title}>Qualité RAG</h2>
        <p className={styles.errorText}>
          Impossible de charger les métriques de qualité.
        </p>
        <p className={styles.requestId}>Requête&nbsp;: {quality.request_id}</p>
      </article>
    );
  }

  if (quality.kind === "no_data") {
    return (
      <article className={styles.card} aria-label="Qualité RAG">
        <h2 className={styles.title}>Qualité RAG</h2>
        <p className={styles.emptyState}>
          Aucune évaluation disponible pour le moment.
        </p>
      </article>
    );
  }

  const indicators: Indicator[] = [
    {
      label: "Fidélité",
      technicalName: "faithfulness",
      explanation:
        "La réponse colle-t-elle aux sources récupérées, sans rien inventer ?",
      value: quality.faithfulness,
    },
    {
      label: "Pertinence",
      technicalName: "answer relevancy",
      explanation: "La réponse répond-elle vraiment à la question posée ?",
      value: quality.answer_relevancy,
    },
    {
      label: "Précision du contexte",
      technicalName: "context precision",
      explanation:
        "Les passages utiles sont-ils placés en tête des sources récupérées ?",
      value: quality.context_precision,
    },
    {
      label: "Couverture du contexte",
      technicalName: "context recall",
      explanation:
        "A-t-on récupéré toutes les sources nécessaires pour répondre ?",
      value: quality.context_recall,
    },
  ];

  return (
    <article className={styles.card} aria-label="Qualité RAG">
      <h2 className={styles.title}>Qualité RAG</h2>
      <div className={styles.meta}>
        <time dateTime={quality.finished_at} className={styles.finishedAt}>
          {formatLastEvaluationDate(quality.finished_at)}
        </time>
        <InfoTooltip label={DATE_TOOLTIP_LABEL} content={DATE_TOOLTIP_CONTENT} />
      </div>
      <div className={styles.gauges}>
        {indicators.map((indicator) => (
          <Gauge key={indicator.technicalName} indicator={indicator} />
        ))}
      </div>
    </article>
  );
}
