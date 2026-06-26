/**
 * RagasGauges — Qualité · Ragas card (issue #252, reworked for #348 / PRD #346).
 *
 * Pure presentational server component: no gateway knowledge, no fetch. Renders
 * the four French-labelled score rows from GET /v1/quality, a threshold legend,
 * and the golden-set / last-eval meta. Imports the InfoTooltip client leaf for
 * the per-indicator and date tooltips (reachable on hover AND keyboard focus).
 *
 * Each row's displayed value and bar are coloured by the quality band returned
 * by classifyRagasScore (the sole logic extracted out of JSX). The band is
 * exposed via `data-band` so CSS can paint green / amber / red without the
 * component knowing the colours.
 *
 * The last-evaluation date is shown as a readable French day/month/year
 * (Europe/Paris). All server-returned strings render as text, never as HTML.
 */
import type { QualityResult } from "@/lib/observability-types";
import { classifyRagasScore, type RagasBand } from "@/lib/ragas-bands";
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

const LEGEND: readonly { band: RagasBand; text: string }[] = [
  { band: "good", text: "≥ 0.85 bon" },
  { band: "fair", text: "0.70–0.85 correct" },
  { band: "weak", text: "< 0.70 faible" },
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

function ScoreRow({ descriptor, value }: { descriptor: IndicatorDescriptor; value: number }) {
  const clamped = Math.min(1, Math.max(0, value));
  const percent = Math.round(clamped * 100);
  const band = classifyRagasScore(value);
  return (
    <div className={styles.row}>
      <span className={styles.rowName}>
        {descriptor.label}
        <InfoTooltip label={descriptor.label} content={tooltipContent(descriptor)} />
      </span>
      <span className={styles.rowValue} data-band={band}>
        {value.toFixed(2)}
      </span>
      <div
        className={styles.rowBar}
        data-band={band}
        role="meter"
        aria-valuenow={value}
        aria-valuemin={0}
        aria-valuemax={1}
      >
        <div className={styles.rowBarFill} data-band={band} style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}

function Legend() {
  return (
    <div className={styles.legend}>
      {LEGEND.map((entry) => (
        <span key={entry.band} className={styles.legendEntry}>
          <i className={styles.legendSwatch} data-band={entry.band} aria-hidden="true" />
          {entry.text}
        </span>
      ))}
    </div>
  );
}

function CardShell({ children }: { children: React.ReactNode }) {
  return (
    <article className={styles.card} aria-label="Qualité RAG">
      <h2 className={styles.title}>Qualité · Ragas</h2>
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
          Pas encore de données : aucune évaluation n’a encore été exécutée.
        </p>
      </CardShell>
    );
  }

  return (
    <CardShell>
      <div className={styles.rows}>
        {INDICATORS.map((descriptor) => (
          <ScoreRow
            key={descriptor.technicalName}
            descriptor={descriptor}
            value={descriptor.score(quality)}
          />
        ))}
      </div>
      <Legend />
      <div className={styles.meta}>
        <span className={styles.metaLine}>
          Golden set <b className={styles.metaValue}>{quality.golden_set_version}</b>
        </span>
        <span className={styles.metaLine}>
          Dernière éval{" "}
          <time dateTime={quality.finished_at} className={styles.metaValue}>
            {dateFormatter.format(new Date(quality.finished_at))}
          </time>
          <InfoTooltip label="À propos de la date d'évaluation" content={DATE_EXPLANATION} />
        </span>
      </div>
    </CardShell>
  );
}
