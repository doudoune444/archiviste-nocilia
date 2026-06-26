/**
 * Ragas score bands — pure classifier for the Qualité · Ragas card (issue #348).
 *
 * Sole piece of logic extracted out of JSX: maps a Ragas score in [0, 1] to a
 * quality band, which in turn drives the gauge colour (green / amber / red).
 *
 * Thresholds match the card legend verbatim:
 *   ≥ 0.85       → "good"  (bon,     green)
 *   0.70 ≤ s < 0.85 → "fair"  (correct, amber)
 *   < 0.70       → "weak"  (faible,  red)
 *
 * Both thresholds are inclusive on their lower bound (0.85 is good, 0.70 is fair),
 * matching the legend's « ≥ 0.85 » and « 0.70–0.85 » wording.
 */

export type RagasBand = "good" | "fair" | "weak";

const GOOD_THRESHOLD = 0.85;
const FAIR_THRESHOLD = 0.7;

export function classifyRagasScore(score: number): RagasBand {
  if (score >= GOOD_THRESHOLD) return "good";
  if (score >= FAIR_THRESHOLD) return "fair";
  return "weak";
}
