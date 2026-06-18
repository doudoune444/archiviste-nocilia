"use client";
/**
 * LoadMoreButton — client component for paginating the board (BOARD-002 AC4).
 *
 * Fetches additional tickets from the internal route handler and appends them
 * to the list. Rendered only when there are more items to load.
 */

import { useState } from "react";
import { TicketTable } from "./TicketTable";
import { BOARD_PAGE_SIZE, isBoardPage } from "./types";
import type { BoardTicket } from "./types";
import styles from "./LoadMoreButton.module.css";

interface LoadMoreButtonProps {
  initialTickets: BoardTicket[];
  total: number;
}

export function LoadMoreButton({ initialTickets, total }: LoadMoreButtonProps) {
  const [tickets, setTickets] = useState<BoardTicket[]>(initialTickets);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const hasMore = tickets.length < total;

  async function loadMore() {
    setIsLoading(true);
    setError(null);

    const params = new URLSearchParams({
      sort: "priority",
      limit: String(BOARD_PAGE_SIZE),
      offset: String(tickets.length),
    });

    try {
      const response = await fetch(`/api/v1/board?${params.toString()}`);
      if (!response.ok) {
        const requestId = response.headers.get("x-request-id") ?? "inconnu";
        setError(`Échec du chargement (id: ${requestId})`);
        return;
      }
      // AC5: guard against a shape-drifted 200 body to prevent undefined.map() crash.
      const body: unknown = await response.json();
      if (!isBoardPage(body)) {
        const requestId = response.headers.get("x-request-id") ?? "inconnu";
        setError(`Réponse inattendue du serveur (id: ${requestId})`);
        return;
      }
      setTickets((prev) => [...prev, ...body.items]);
    } catch {
      setError("Erreur réseau. Veuillez réessayer.");
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <div>
      <TicketTable tickets={tickets} />
      {error !== null && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      {hasMore && (
        <div className={styles.loadMoreRow}>
          <button
            className={styles.loadMoreBtn}
            onClick={loadMore}
            disabled={isLoading}
            aria-busy={isLoading}
          >
            {isLoading ? "Chargement…" : "Charger plus"}
          </button>
        </div>
      )}
    </div>
  );
}
