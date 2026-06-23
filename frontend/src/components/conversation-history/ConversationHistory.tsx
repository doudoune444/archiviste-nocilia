"use client";
/**
 * ConversationHistory — the past-conversations list inside the sidebar (#245).
 *
 * Lists the caller's past conversations (owner-scoped via bff-proxy cookie).
 * Emits "select" upward via a callback — no routing side-effects. Rendered only
 * on the chat page (the shell decides). The "Nouvelle conversation" button lives
 * in the shell (always visible, above the history), not here.
 *
 * #245: each item displays the conversation TITLE only (start of the first user
 * message, server-derived), on a single truncated line — no date, no count.
 *
 * A01: identity never passed from client; cookie is the source of truth.
 * A09: conversation content is never logged.
 */

import styles from "./ConversationHistory.module.css";
import type { ConversationSummary } from "./types";

const LABEL_HISTORY = "Historique";
const LABEL_EMPTY = "Aucune conversation passée";
const LABEL_UNTITLED = "Conversation sans titre";

interface ConversationHistoryProps {
  conversations: ConversationSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function ConversationHistory({
  conversations,
  selectedId,
  onSelect,
}: ConversationHistoryProps) {
  return (
    <nav className={styles.sidebar} aria-label={LABEL_HISTORY}>
      <span className={styles.sidebarHeading}>{LABEL_HISTORY}</span>

      {conversations.length === 0 ? (
        <span className={styles.empty}>{LABEL_EMPTY}</span>
      ) : (
        conversations.map((conversation) => (
          <button
            key={conversation.id}
            type="button"
            className={styles.conversationItem}
            aria-current={selectedId === conversation.id ? "true" : undefined}
            onClick={() => onSelect(conversation.id)}
            data-testid={`conversation-item-${conversation.id}`}
          >
            <span className={styles.title}>
              {(conversation.title ?? "").trim() === ""
                ? LABEL_UNTITLED
                : conversation.title}
            </span>
          </button>
        ))
      )}
    </nav>
  );
}
