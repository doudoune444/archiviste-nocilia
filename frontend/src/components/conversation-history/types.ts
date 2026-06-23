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
  /** Derived at read time by the gateway: start of the first user message (#250). */
  title: string;
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
}

/** Type guard: does an unknown value look like a ConversationSummary array? */
export function isConversationList(
  value: unknown
): value is { conversations: ConversationSummary[] } {
  if (typeof value !== "object" || value === null) return false;
  const obj = value as Record<string, unknown>;
  if (!Array.isArray(obj["conversations"])) return false;
  return true;
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
