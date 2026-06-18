/**
 * RagasGauges — presentational component for GET /v1/quality data.
 *
 * Pure: no gateway knowledge, no fetch calls.
 * Renders four labeled score gauges (0..1) when metrics are present,
 * a clean empty state when no eval has run, or an error state with a request id.
 * All server-returned strings rendered as text — never dangerouslySetInnerHTML.
 */
import type { QualityResult } from "@/lib/observability-types";
import styles from "./RagasGauges.module.css";

interface RagasGaugesProps {
  quality: QualityResult;
}

interface GaugeProps {
  label: string;
  value: number;
}

function Gauge({ label, value }: GaugeProps) {
  // Clamp to [0, 1] so a score outside the valid range never produces a
  // negative width or a bar wider than the container.
  const clamped = Math.min(1, Math.max(0, value));
  const percent = Math.round(clamped * 100);
  return (
    <div className={styles.gauge}>
      <span className={styles.gaugeLabel}>{label}</span>
      <div className={styles.gaugeBar} role="meter" aria-valuenow={value} aria-valuemin={0} aria-valuemax={1}>
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
        <span className={styles.version}>{quality.golden_set_version}</span>
        <span className={styles.separator}>·</span>
        <time dateTime={quality.finished_at} className={styles.finishedAt}>
          {quality.finished_at}
        </time>
      </div>
      <div className={styles.gauges}>
        <Gauge label="Faithfulness" value={quality.faithfulness} />
        <Gauge label="Answer Relevancy" value={quality.answer_relevancy} />
        <Gauge label="Context Precision" value={quality.context_precision} />
        <Gauge label="Context Recall" value={quality.context_recall} />
      </div>
    </article>
  );
}
