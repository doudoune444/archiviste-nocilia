"use client";
/**
 * DashboardTickets — client wrapper for the author dashboard (DASH-002).
 *
 * Owns drawer state (`openTicket`) and wires the LoadMoreButton's
 * `onOpenTranscript` prop to a TranscriptDrawer. The dashboard RSC
 * (`src/app/dashboard/page.tsx`) is a Server Component and cannot hold
 * client state — this wrapper is the boundary.
 *
 * Layout on laptop: flex row so the drawer sits alongside the ticket list
 * without covering it. On narrow viewports the drawer overlays (CSS handles
 * the difference; the component tree is identical).
 */

import { useState, useCallback } from "react";
import { LoadMoreButton } from "@/components/board/LoadMoreButton";
import { TranscriptDrawer, fetchTranscript } from "./TranscriptDrawer";
import type { BoardTicket } from "@/components/board/types";
import type { BoardFilter } from "@/components/category-filter/params";
import type { Message } from "@/components/conversation-history/types";
import styles from "./DashboardTickets.module.css";

type DrawerState =
  | { status: "loading" }
  | { status: "error"; requestId: string }
  | { status: "loaded"; messages: Message[] };

interface DashboardTicketsProps {
  initialTickets: BoardTicket[];
  total: number;
  filter: BoardFilter;
  apiPath: string;
}

export function DashboardTickets({
  initialTickets,
  total,
  filter,
  apiPath,
}: DashboardTicketsProps) {
  const [openTicket, setOpenTicket] = useState<BoardTicket | null>(null);
  const [drawerState, setDrawerState] = useState<DrawerState>({
    status: "loading",
  });

  const handleOpenTranscript = useCallback(async (ticket: BoardTicket) => {
    setOpenTicket(ticket);
    setDrawerState({ status: "loading" });

    const result = await fetchTranscript(ticket.conversation_id);
    if (result.ok) {
      setDrawerState({ status: "loaded", messages: result.messages });
    } else {
      setDrawerState({ status: "error", requestId: result.requestId });
    }
  }, []);

  const handleClose = useCallback(() => {
    setOpenTicket(null);
  }, []);

  return (
    <div
      className={openTicket !== null ? styles.wrapperWithDrawer : styles.wrapper}
    >
      <div className={styles.listPane}>
        <LoadMoreButton
          initialTickets={initialTickets}
          total={total}
          filter={filter}
          apiPath={apiPath}
          onOpenTranscript={handleOpenTranscript}
        />
      </div>
      {openTicket !== null && (
        <TranscriptDrawer
          ticket={openTicket}
          drawerState={drawerState}
          onClose={handleClose}
        />
      )}
    </div>
  );
}
