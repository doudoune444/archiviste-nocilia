/**
 * ConfirmationBadge — FIX-BADGE truthfulness invariant (BOARD-002 AC2).
 *
 * INVARIANT: renders "non confirmé par les juges" ONLY when judges_not_passed
 * is strictly true. ALL other cases (false, undefined, legacy null) render
 * neutrally — NO affirmative "confirmé" claim is ever shown.
 * This is not cosmetic: a false positive would assert something unproven.
 */

import styles from "./ConfirmationBadge.module.css";

interface ConfirmationBadgeProps {
  judges_not_passed: boolean | undefined;
}

export function ConfirmationBadge({
  judges_not_passed,
}: ConfirmationBadgeProps) {
  if (judges_not_passed !== true) {
    // AC2: neutral — never show an affirmative "confirmé" claim.
    return null;
  }

  return (
    <span className={styles.badge} data-testid="badge-not-confirmed">
      non confirmé par les juges
    </span>
  );
}
