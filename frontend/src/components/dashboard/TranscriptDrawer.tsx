"use client";
/**
 * TranscriptDrawer — author-only slide-over panel (DASH-002).
 *
 * Fetches the conversation turns for the given ticket, validates the response
 * with the shared `isConversationMessages` guard, maps them with
 * `mapTranscriptToMessages`, and renders each turn through `AssistantAnswer`
 * (sanitized Markdown — no dangerouslySetInnerHTML).
 *
 * Security (security.md §Output sanitization):
 * - All turn content rendered through AssistantAnswer (react-markdown + rehype-sanitize).
 * - Fetch key is `ticket.conversation_id` (server-owned UUID), never user free-text.
 * - Error messages never leak gateway internals — only the request id is surfaced.
 *
 * Keyboard: ESC closes the drawer. Backdrop click also closes.
 */

import { useEffect, useRef, useCallback } from "react";
import AssistantAnswer from "@/components/assistant-answer/AssistantAnswer";
import {
  isConversationMessages,
  type Message,
} from "@/components/conversation-history/types";
import { mapTranscriptToMessages } from "@/components/conversation-history/transcript";
import type { BoardTicket } from "@/components/board/types";
import styles from "./TranscriptDrawer.module.css";

type DrawerState =
  | { status: "loading" }
  | { status: "error"; requestId: string }
  | { status: "loaded"; messages: Message[] };

interface TranscriptDrawerProps {
  ticket: BoardTicket;
  drawerState: DrawerState;
  onClose: () => void;
}

function TurnItem({ message }: { message: Message }) {
  const label = message.role === "user" ? "Vous" : "Archiviste";
  return (
    <div className={styles.turn}>
      <span className={styles.turnLabel} data-role={message.role}>
        {label}
      </span>
      <div className={styles.turnContent}>
        <AssistantAnswer
          text={message.text}
          mode={undefined}
          citations={undefined}
        />
      </div>
    </div>
  );
}

export function TranscriptDrawer({
  ticket,
  drawerState,
  onClose,
}: TranscriptDrawerProps) {
  const closeRef = useRef<HTMLButtonElement>(null);

  // Close on ESC keypress
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose]);

  // Focus the close button when the drawer opens
  useEffect(() => {
    closeRef.current?.focus();
  }, [ticket.id]);

  const handleBackdropClick = useCallback(() => {
    onClose();
  }, [onClose]);

  return (
    <>
      {/* Backdrop: visible on narrow viewports only (CSS media query hides it on laptop) */}
      <div
        className={styles.backdrop}
        onClick={handleBackdropClick}
        aria-hidden="true"
        data-testid="drawer-backdrop"
      />
      <aside
        className={styles.drawer}
        aria-label="Transcript de la conversation"
        data-testid="transcript-drawer"
      >
        <header className={styles.header}>
          <h2 className={styles.title}>Transcript</h2>
          <button
            ref={closeRef}
            className={styles.closeBtn}
            onClick={onClose}
            aria-label="Fermer le transcript"
            data-testid="close-drawer-btn"
          >
            Fermer
          </button>
        </header>
        <div className={styles.body} data-testid="drawer-body">
          {drawerState.status === "loading" && (
            <p className={styles.status} role="status">
              Chargement…
            </p>
          )}
          {drawerState.status === "error" && (
            <p className={styles.error} role="alert" data-testid="drawer-error">
              Impossible de charger le transcript.{" "}
              <span className={styles.requestId}>
                (id&nbsp;: {drawerState.requestId})
              </span>
            </p>
          )}
          {drawerState.status === "loaded" &&
            drawerState.messages.map((message, index) => (
              // index is stable for a given loaded transcript (ordinal-sorted by mapTranscriptToMessages)
              <TurnItem key={index} message={message} />
            ))}
        </div>
      </aside>
    </>
  );
}

/** Fetches messages for a conversation and returns them as mapped Message[]. */
export async function fetchTranscript(
  conversationId: string
): Promise<{ ok: true; messages: Message[] } | { ok: false; requestId: string }> {
  let response: Response;
  try {
    response = await fetch(
      `/api/v1/conversations/${conversationId}/messages`
    );
  } catch {
    return { ok: false, requestId: "inconnu" };
  }

  if (!response.ok) {
    const requestId = response.headers.get("x-request-id") ?? "inconnu";
    return { ok: false, requestId };
  }

  const body: unknown = await response.json().catch(() => null);
  if (!isConversationMessages(body)) {
    const requestId = response.headers.get("x-request-id") ?? "inconnu";
    return { ok: false, requestId };
  }

  return {
    ok: true,
    messages: mapTranscriptToMessages(body.messages),
  };
}
