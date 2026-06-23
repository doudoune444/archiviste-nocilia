"use client";
/**
 * ConversationHistory sidebar (CHAT-004).
 *
 * Lists the caller's past conversations (owner-scoped via bff-proxy cookie).
 * Emits "select" / "new" actions upward via callbacks — no routing side-effects.
 *
 * AC: sidebar lists conversations for anonymous AND member callers.
 * AC: clicking a past conversation fires onSelect(id).
 * AC: "Nouvelle conversation" fires onNew().
 * A01: identity never passed from client; cookie is the source of truth.
 * A09: conversation content is never logged.
 */

import styles from "./ConversationHistory.module.css";
import type { ConversationSummary } from "./types";

const LABEL_NEW_CONVERSATION = "Nouvelle conversation";
const LABEL_HISTORY = "Historique";
const LABEL_EMPTY = "Aucune conversation passée";

interface ConversationHistoryProps {
  conversations: ConversationSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
}

const LABEL_UNTITLED = "Conversation sans titre";

export function ConversationHistory({
  conversations,
  selectedId,
  onSelect,
  onNew,
}: ConversationHistoryProps) {
  return (
    <nav className={styles.sidebar} aria-label={LABEL_HISTORY}>
      <span className={styles.sidebarHeading}>{LABEL_HISTORY}</span>

      <button
        type="button"
        className={styles.newButton}
        onClick={onNew}
        data-testid="new-conversation-btn"
      >
        {LABEL_NEW_CONVERSATION}
      </button>

      {conversations.length === 0 ? (
        <span className={styles.empty}>{LABEL_EMPTY}</span>
      ) : (
        conversations.map((conv) => (
          <button
            key={conv.id}
            type="button"
            className={styles.conversationItem}
            aria-current={selectedId === conv.id ? "true" : undefined}
            onClick={() => onSelect(conv.id)}
            data-testid={`conversation-item-${conv.id}`}
            title={conv.title || LABEL_UNTITLED}
          >
            <span className={styles.title}>
              {conv.title || LABEL_UNTITLED}
            </span>
          </button>
        ))
      )}
    </nav>
  );
}
