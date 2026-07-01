/**
 * Pure transcript→Message mapping (CHAT-004, #375).
 *
 * Converts the gateway ConversationMessage rows (role, ordinal, content) into
 * the chat thread's Message type. Ordinal order is ascending.
 *
 * #375 (PRD #372): an assistant row is re-hydrated from its persisted `content`
 * so a reloaded turn renders like the freshly-streamed one — the `---SUIVI---`
 * block becomes follow-up pills, inline `[source_path]` markers become citations
 * (superscripts + sources panel), and the conversation id is attached so the
 * per-answer signal form re-appears. `mode` is never persisted, so it stays unset.
 *
 * A09: never logs content — callers must not log the returned messages.
 */

import { parsePersistedAnswer } from "./persisted-answer";
import type { ConversationMessage, Message } from "./types";

/** Re-hydrates a persisted assistant turn into a rich Message. */
function assistantMessage(content: string, conversationId: string): Message {
  const { text, followups, citations } = parsePersistedAnswer(content);
  const message: Message = { role: "assistant", text, conversationId };
  if (followups.length > 0) {
    message.followups = followups;
  }
  if (citations.length > 0) {
    message.citations = citations;
  }
  return message;
}

/**
 * Maps an array of gateway transcript rows to chat thread Messages, sorted by
 * ordinal ascending (the gateway guarantees the order; this is defensive).
 *
 * `conversationId` is the owning conversation's id (GET response `conversation_id`)
 * — attached to assistant turns so the per-answer signal form re-hydrates on reload.
 */
export function mapTranscriptToMessages(
  rows: ConversationMessage[],
  conversationId: string
): Message[] {
  const sorted = [...rows].sort((a, b) => a.ordinal - b.ordinal);
  return sorted.map((row) =>
    row.role === "user"
      ? { role: "user", text: row.content }
      : assistantMessage(row.content, conversationId)
  );
}
