/**
 * Pure transcript→Message mapping (CHAT-004).
 *
 * Converts the gateway ConversationMessage rows (role, ordinal, content) into
 * the chat thread's Message type (role, text). Ordinal order is ascending.
 *
 * A09: never logs content — callers must not log the returned messages.
 */

import type { ConversationMessage, Message } from "./types";

/**
 * Maps an array of gateway transcript rows to chat thread Messages.
 *
 * Rows are sorted by ordinal ascending so the caller can pass them in any order
 * (the gateway guarantees ordinal order, but this is defensive).
 *
 * Only `role` and `content` are consumed — optional fields (mode, citations,
 * conversationId) are intentionally left out; other tickets fill them.
 */
export function mapTranscriptToMessages(rows: ConversationMessage[]): Message[] {
  const sorted = [...rows].sort((a, b) => a.ordinal - b.ordinal);
  return sorted.map((row) => ({
    role: row.role === "user" ? "user" : "assistant",
    text: row.content,
  }));
}
