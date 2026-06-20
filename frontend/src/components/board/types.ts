/**
 * Shared types for the lore-gap board (BOARD-002).
 * Used by TicketTable, ConfirmationBadge, and the board RSC page.
 */

/** Number of tickets fetched per page — single source of truth (BOARD-002). */
export const BOARD_PAGE_SIZE = 20;

export interface BoardTicket {
  id: string;
  conversation_id: string;
  question: string;
  category: string;
  priority_score: number;
  status: string;
  created_at: string;
  updated_at: string;
  judges_not_passed: boolean;
}

export interface BoardPage {
  items: BoardTicket[];
  total: number;
  limit: number;
  offset: number;
  /** Distinct sorted category values across ALL open tickets (#231). */
  categories: string[];
}

/**
 * Minimal runtime shape guard — checks the fields that callers depend on
 * before casting an unknown response body to BoardPage.
 * No zod dependency: per ADR, heavy deps require an ADR ticket.
 */
export function isBoardPage(x: unknown): x is BoardPage {
  return (
    typeof x === "object" &&
    x !== null &&
    Array.isArray((x as BoardPage).items) &&
    typeof (x as BoardPage).total === "number" &&
    Array.isArray((x as BoardPage).categories)
  );
}
