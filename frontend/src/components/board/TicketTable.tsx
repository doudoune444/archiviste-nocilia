/**
 * TicketTable — pure presentational table for lore-gap board (BOARD-002 AC3).
 *
 * Security: question and category are rendered as React text nodes only.
 * NEVER dangerouslySetInnerHTML — React auto-escaping is the only sanitiser.
 */

import { ConfirmationBadge } from "./ConfirmationBadge";
import type { BoardTicket } from "./types";
import styles from "./TicketTable.module.css";

const COLUMN_COUNT = 5;

interface TicketTableProps {
  tickets: BoardTicket[];
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

function TicketRow({ ticket }: { ticket: BoardTicket }) {
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
        {/* AC3: category as chip, text only — no dangerouslySetInnerHTML */}
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
    </tr>
  );
}

export function TicketTable({ tickets }: TicketTableProps) {
  return (
    <table className={styles.table} aria-label="tickets lore-gap">
      <thead>
        <tr>
          <th className={styles.th} scope="col">Priorité</th>
          <th className={styles.th} scope="col">Catégorie</th>
          <th className={styles.th} scope="col">Question</th>
          <th className={styles.th} scope="col">Date</th>
          <th className={styles.th} scope="col">Confirmation</th>
        </tr>
      </thead>
      <tbody>
        {tickets.length === 0 ? (
          // AC4: empty-board state spans ALL columns
          <tr>
            <td
              className={styles.empty}
              colSpan={COLUMN_COUNT}
              data-testid="empty-board"
            >
              Aucun ticket ouvert pour le moment.
            </td>
          </tr>
        ) : (
          tickets.map((ticket) => (
            <TicketRow key={ticket.id} ticket={ticket} />
          ))
        )}
      </tbody>
    </table>
  );
}
