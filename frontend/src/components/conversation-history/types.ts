/**
 * Shared types for the conversation-history sidebar (CHAT-004).
 *
 * ConversationSummary — one row from GET /v1/conversations.
 * ConversationMessage — one turn from GET /v1/conversations/{id}/messages.
 *
 * A09: these types never carry user identity — identity comes from the cookie
 * forwarded by bff-proxy (server is the source of truth).
 */

/** One conversation summary as returned by GET /v1/conversations. */
export interface ConversationSummary {
  id: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  /** Readable label derived by the gateway from the first user message (#250). */
  title: string;
  /**
   * True when a signalement (ticket) references this conversation. The sidebar uses
   * it to proactively disable the delete icon — deletion is blocked while flagged (#284).
   */
  has_ticket: boolean;
}

/** One message turn as returned by GET /v1/conversations/{id}/messages. */
export interface ConversationMessage {
  role: string;
  ordinal: number;
  content: string;
}

/** Shared Message type used by the chat thread (kept minimal; other fields filled by parallel tickets). */
export interface Message {
  role: "user" | "assistant";
  text: string;
  mode?: string;
  citations?: unknown[];
  conversationId?: string;
  /** #355/#375: structured follow-up questions rendered as clickable pills. */
  followups?: string[];
}

/** Type guard: does an unknown value look like a ConversationSummary array? */
export function isConversationList(
  value: unknown
): value is { conversations: ConversationSummary[] } {
  if (typeof value !== "object" || value === null) return false;
  const obj = value as Record<string, unknown>;
  if (!Array.isArray(obj["conversations"])) return false;
  return obj["conversations"].every(isConversationSummary);
}

/** Runtime shape guard for a single summary — only the fields callers depend on. */
function isConversationSummary(value: unknown): value is ConversationSummary {
  if (typeof value !== "object" || value === null) return false;
  const obj = value as Record<string, unknown>;
  return (
    typeof obj["id"] === "string" &&
    typeof obj["title"] === "string" &&
    typeof obj["has_ticket"] === "boolean"
  );
}

/** Type guard: does an unknown value look like a ConversationMessagesResponse? */
export function isConversationMessages(
  value: unknown
): value is { conversation_id: string; messages: ConversationMessage[] } {
  if (typeof value !== "object" || value === null) return false;
  const obj = value as Record<string, unknown>;
  if (typeof obj["conversation_id"] !== "string") return false;
  if (!Array.isArray(obj["messages"])) return false;
  return true;
}
