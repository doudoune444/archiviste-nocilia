/**
 * DecisionsSection — "Décisions & pistes d'amélioration" editorial section (#351).
 *
 * Pure presentational server component (RSC). All prose is imported from the
 * versioned content module (`decisions-content`), keeping the wording editable
 * without touching this layout. Rich paragraphs are rendered from segment arrays
 * — inline `code` spans are real <code> elements; no raw HTML is injected.
 *
 * Each decision is a list item carrying a numbered badge, a title, a monospace
 * technical kicker, a state paragraph, and a "Piste d'amélioration" inset.
 */
import {
  DECISIONS,
  DECISIONS_TITLE,
  DECISIONS_SUBTITLE,
  IMPROVEMENT_LABEL,
  type Decision,
  type ProseSegment,
} from "./decisions-content";
import styles from "./DecisionsSection.module.css";

function Prose({ segments }: { segments: readonly ProseSegment[] }) {
  return (
    <>
      {segments.map((segment, index) =>
        segment.kind === "code" ? (
          <code key={index} className={styles.code}>
            {segment.text}
          </code>
        ) : (
          <span key={index}>{segment.text}</span>
        )
      )}
    </>
  );
}

function DecisionCard({
  decision,
  number,
}: {
  decision: Decision;
  number: number;
}) {
  return (
    <li className={styles.card}>
      <span className={styles.badge} aria-hidden="true">
        {number}
      </span>
      <div className={styles.headRow}>
        <h3 className={styles.title}>{decision.title}</h3>
        <span className={styles.kicker}>{decision.kicker}</span>
      </div>
      <p className={styles.state}>
        <Prose segments={decision.state} />
      </p>
      <div className={styles.improvement}>
        <span className={styles.improvementLabel}>{IMPROVEMENT_LABEL}</span>
        <p>
          <Prose segments={decision.improvement} />
        </p>
      </div>
    </li>
  );
}

export function DecisionsSection() {
  return (
    <section className={styles.section} aria-labelledby="decisions-heading">
      <div className={styles.head}>
        <h2 id="decisions-heading" className={styles.heading}>
          {DECISIONS_TITLE}
        </h2>
        <p className={styles.subtitle}>{DECISIONS_SUBTITLE}</p>
      </div>
      <ol className={styles.list}>
        {DECISIONS.map((decision, index) => (
          <DecisionCard
            key={decision.title}
            decision={decision}
            number={index + 1}
          />
        ))}
      </ol>
    </section>
  );
}
