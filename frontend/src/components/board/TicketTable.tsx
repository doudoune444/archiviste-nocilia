/**
 * TicketTable — pure presentational table for lore-gap board (BOARD-002 AC3).
 *
 * DASH-002: opt-in per-row transcript affordance via `onOpenTranscript` prop.
 * The public board passes nothing → no button, zero behavior change.
 *
 * Security: question and category are rendered as React text nodes only.
 * NEVER dangerouslySetInnerHTML — React auto-escaping is the only sanitiser.
 */

import { ConfirmationBadge } from "./ConfirmationBadge";
import type { BoardTicket } from "./types";
import styles from "./TicketTable.module.css";

/** Displayed when the transcript column header is visible (author dashboard only). */
const TRANSCRIPT_HEADER_LABEL = "Transcript";

/** Column count without the transcript column. */
const BASE_COLUMN_COUNT = 5;

interface TicketTableProps {
  tickets: BoardTicket[];
  /**
   * DASH-002: when provided, adds a per-row "open transcript" button.
   * Public board omits this prop → button never rendered.
   */
  onOpenTranscript?: (ticket: BoardTicket) => void;
}

function derivePriorityLevel(priority_score: number): "high" | "medium" | "low" {
  if (priority_score >= 5) return "high";
  if (priority_score >= 2) return "medium";
  return "low";
}

function formatDate(isoString: string): string {
  const date = new Date(isoString);
  return date.toLocaleDateString("fr-FR", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

interface TicketRowProps {
  ticket: BoardTicket;
  onOpenTranscript?: (ticket: BoardTicket) => void;
}

function TicketRow({ ticket, onOpenTranscript }: TicketRowProps) {
  const priorityLevel = derivePriorityLevel(ticket.priority_score);
  return (
    <tr className={styles.row}>
      <td className={styles.cell}>
        {/* AC3: priority as visual intensity via data attribute */}
        <span
          className={styles.priority}
          data-level={priorityLevel}
          aria-label={`priorité ${ticket.priority_score}`}
        >
          {ticket.priority_score}
        </span>
      </td>
      <td className={styles.cell}>
        {/* AC3: category as chip, text only — React auto-escape only */}
        <span className={styles.chip}>{ticket.category}</span>
      </td>
      <td className={styles.cell}>
        {/* AC3 + security: question is untrusted text — React auto-escape only */}
        <span className={styles.question}>{ticket.question}</span>
      </td>
      <td className={styles.cell}>
        <time dateTime={ticket.created_at}>{formatDate(ticket.created_at)}</time>
      </td>
      <td className={styles.cell}>
        <ConfirmationBadge judges_not_passed={ticket.judges_not_passed} />
      </td>
      {onOpenTranscript !== undefined && (
        <td className={styles.cell}>
          {/* DASH-002: author-only transcript affordance — not rendered on public board */}
          <button
            className={styles.transcriptBtn}
            onClick={() => onOpenTranscript(ticket)}
            data-testid="open-transcript-btn"
            aria-label={`Ouvrir le transcript du ticket ${ticket.id}`}
          >
            Transcript
          </button>
        </td>
      )}
    </tr>
  );
}

export function TicketTable({ tickets, onOpenTranscript }: TicketTableProps) {
  const columnCount =
    onOpenTranscript !== undefined ? BASE_COLUMN_COUNT + 1 : BASE_COLUMN_COUNT;
  return (
    <table className={styles.table} aria-label="tickets lore-gap">
      <thead>
        <tr>
          <th className={styles.th} scope="col">Priorité</th>
          <th className={styles.th} scope="col">Catégorie</th>
          <th className={styles.th} scope="col">Question</th>
          <th className={styles.th} scope="col">Date</th>
          <th className={styles.th} scope="col">Confirmation</th>
          {onOpenTranscript !== undefined && (
            <th className={styles.th} scope="col">{TRANSCRIPT_HEADER_LABEL}</th>
          )}
        </tr>
      </thead>
      <tbody>
        {tickets.length === 0 ? (
          // AC4: empty-board state spans ALL columns (including transcript col when present)
          <tr>
            <td
              className={styles.empty}
              colSpan={columnCount}
              data-testid="empty-board"
            >
              Aucun ticket ouvert pour le moment.
            </td>
          </tr>
        ) : (
          tickets.map((ticket) => (
            <TicketRow
              key={ticket.id}
              ticket={ticket}
              onOpenTranscript={onOpenTranscript}
            />
          ))
        )}
      </tbody>
    </table>
  );
}
