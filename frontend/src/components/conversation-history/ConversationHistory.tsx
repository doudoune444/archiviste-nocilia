"use client";
/**
 * ConversationHistory sidebar (CHAT-004, delete flow #287).
 *
 * Lists the caller's past conversations (owner-scoped via bff-proxy cookie).
 * Emits "select" / "request-delete" actions upward via callbacks — no routing
 * side-effects, no network. The actual DELETE + UI reconcile lives in ChatShell.
 *
 * AC: sidebar lists conversations for anonymous AND member callers.
 * AC: clicking a past conversation fires onSelect(id).
 * AC #287: each row carries a trash button → onRequestDelete(id); it is disabled
 *   with an explanatory tooltip when the conversation carries a signalement
 *   (has_ticket), and the whole row is greyed + busy + disabled while its own
 *   delete is in flight (deletingId).
 * A01: identity never passed from client; cookie is the source of truth.
 * A09: conversation content is never logged.
 *
 * #248: the "Nouvelle conversation" action now lives in the global sidebar
 * app-shell, so this component renders only the history list.
 */

import styles from "./ConversationHistory.module.css";
import { TrashIcon } from "@/components/confirm-dialog/TrashIcon";
import type { ConversationSummary } from "./types";

const LABEL_HISTORY = "Conversations récentes";
const LABEL_EMPTY = "Aucune conversation passée";
const LABEL_DELETE = "Supprimer la conversation";
const TOOLTIP_TICKET_BLOCKED =
  "Suppression impossible : un signalement est en cours sur cette conversation.";

interface ConversationHistoryProps {
  conversations: ConversationSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onRequestDelete: (id: string) => void;
  deletingId: string | null;
}

export function ConversationHistory({
  conversations,
  selectedId,
  onSelect,
  onRequestDelete,
  deletingId,
}: ConversationHistoryProps) {
  return (
    <nav className={styles.sidebar} aria-label={LABEL_HISTORY}>
      <span className={styles.sidebarHeading}>{LABEL_HISTORY}</span>

      {conversations.length === 0 ? (
        <span className={styles.empty}>{LABEL_EMPTY}</span>
      ) : (
        conversations.map((conv) => {
          const isDeleting = deletingId === conv.id;
          const isActive = selectedId === conv.id;
          const itemClassName = isActive
            ? `${styles.conversationItem} ${styles.active}`
            : styles.conversationItem;
          return (
            <div key={conv.id} className={styles.row}>
              <button
                type="button"
                className={itemClassName}
                aria-current={isActive ? "true" : undefined}
                aria-busy={isDeleting ? "true" : undefined}
                disabled={isDeleting}
                onClick={() => onSelect(conv.id)}
                data-testid={`conversation-item-${conv.id}`}
              >
                <span className={styles.title}>{conv.title}</span>
                {isDeleting && (
                  <span
                    className={styles.spinner}
                    role="status"
                    aria-label="Suppression en cours"
                  />
                )}
              </button>
              <button
                type="button"
                className={styles.trash}
                aria-label={LABEL_DELETE}
                title={conv.has_ticket ? TOOLTIP_TICKET_BLOCKED : undefined}
                disabled={conv.has_ticket || isDeleting}
                onClick={() => onRequestDelete(conv.id)}
              >
                <TrashIcon />
              </button>
            </div>
          );
        })
      )}
    </nav>
  );
}
