/**
 * RagasGauges — presentational component for GET /v1/quality data.
 *
 * Pure: no gateway knowledge, no fetch calls. Server component (RSC) that
 * imports the InfoTooltip leaf client for the per-metric / per-date help.
 *
 * Renders four labeled score gauges (0..1) when metrics are present, a clean
 * empty state when no eval has run, or an error state with a request id.
 * All server-returned strings rendered as text — never dangerouslySetInnerHTML.
 *
 * Lot 1 (#246): French metric labels, readable French date (no time, no ISO),
 * version hash dropped from display, info tooltips on each metric and the date.
 */
import type { QualityResult } from "@/lib/observability-types";
import { InfoTooltip } from "@/components/info-tooltip/InfoTooltip";
import styles from "./RagasGauges.module.css";

interface RagasGaugesProps {
  quality: QualityResult;
}

interface MetricDescriptor {
  label: string;
  technicalName: string;
  explanation: string;
}

const FAITHFULNESS: MetricDescriptor = {
  label: "Fidélité",
  technicalName: "faithfulness",
  explanation: "La réponse colle-t-elle aux sources récupérées, sans rien inventer ?",
};
const ANSWER_RELEVANCY: MetricDescriptor = {
  label: "Pertinence",
  technicalName: "answer relevancy",
  explanation: "La réponse répond-elle vraiment à la question posée ?",
};
const CONTEXT_PRECISION: MetricDescriptor = {
  label: "Précision du contexte",
  technicalName: "context precision",
  explanation: "Les passages utiles sont-ils placés en tête des sources récupérées ?",
};
const CONTEXT_RECALL: MetricDescriptor = {
  label: "Couverture du contexte",
  technicalName: "context recall",
  explanation: "A-t-on récupéré toutes les sources nécessaires pour répondre ?",
};

const DATE_EXPLANATION =
  "Date de la dernière évaluation automatique de la qualité du RAG.";

const dateFormatter = new Intl.DateTimeFormat("fr-FR", {
  day: "numeric",
  month: "long",
  year: "numeric",
  timeZone: "Europe/Paris",
});

function formatEvaluationDate(finishedAt: string): string {
  return dateFormatter.format(new Date(finishedAt));
}

function metricTooltip(metric: MetricDescriptor): string {
  return `${metric.label} (${metric.technicalName}) — ${metric.explanation}`;
}

interface GaugeProps {
  metric: MetricDescriptor;
  value: number;
}

function Gauge({ metric, value }: GaugeProps) {
  // Clamp to [0, 1] so a score outside the valid range never produces a
  // negative width or a bar wider than the container.
  const clamped = Math.min(1, Math.max(0, value));
  const percent = Math.round(clamped * 100);
  return (
    <div className={styles.gauge}>
      <span className={styles.gaugeLabel}>
        {metric.label}
        <InfoTooltip
          label={`En savoir plus sur ${metric.label}`}
          explanation={metricTooltip(metric)}
        />
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

  return (
    <article className={styles.card} aria-label="Qualité RAG">
      <h2 className={styles.title}>Qualité RAG</h2>
      <div className={styles.meta}>
        <time dateTime={quality.finished_at} className={styles.finishedAt}>
          {formatEvaluationDate(quality.finished_at)}
        </time>
        <InfoTooltip
          label="À propos de la date de dernière évaluation"
          explanation={DATE_EXPLANATION}
        />
      </div>
      <div className={styles.gauges}>
        <Gauge metric={FAITHFULNESS} value={quality.faithfulness} />
        <Gauge metric={ANSWER_RELEVANCY} value={quality.answer_relevancy} />
        <Gauge metric={CONTEXT_PRECISION} value={quality.context_precision} />
        <Gauge metric={CONTEXT_RECALL} value={quality.context_recall} />
      </div>
    </article>
  );
}
