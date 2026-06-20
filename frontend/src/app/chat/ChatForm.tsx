"use client";
/**
 * ChatForm — streaming chat input/output (CHAT-002 + CHAT-003 + CHAT-004).
 *
 * Manages form state, optimistic user message echo, and incremental assistant
 * answer rendering via the SSE consumer.
 *
 * CHAT-004 additions:
 * - Accepts `initialMessages` to render a loaded transcript on mount (no re-render loop).
 * - Calls `onConversationListChange` after the first message sends so the sidebar
 *   can refresh its list from the BFF.
 *
 * AC-scope (CHAT-002): token-by-token rendering, streaming indicator, optimistic
 * echo, double-submit guard, single French error message on failure.
 * AC-scope (CHAT-003): committed assistant answers rendered as sanitized Markdown;
 *   meta.mode and done.citations captured and surfaced in AssistantAnswer.
 * AC-scope (CHAT-004): data-testid="assistant-answer" kept intact (lives on AssistantAnswer).
 *
 * A09: query text is never logged.
 * A03: LLM output rendered via AssistantAnswer (react-markdown + rehype-sanitize).
 *      Plain streaming text uses pre-wrap — never dangerouslySetInnerHTML.
 */

import { useState, useCallback } from "react";
import { consumeSseStream } from "@/lib/sse-stream";
import AssistantAnswer from "@/components/assistant-answer/AssistantAnswer";
import type { ConversationSummary } from "@/components/conversation-history/types";
import styles from "./chat.module.css";

const CHAT_STREAM_PATH = "/api/v1/chat/stream";
const CONVERSATIONS_PATH = "/api/v1/conversations";

/** French error message shown on any network or backend failure. */
const ERROR_MESSAGE_FRENCH =
  "Une erreur est survenue. Veuillez réessayer dans quelques instants.";

/** Shared Message type (CHAT-004): role + text are filled here; other fields by parallel tickets. */
export interface Message {
  role: "user" | "assistant";
  text: string;
  mode?: string;
  citations?: unknown[];
  conversationId?: string;
}

interface ChatFormProps {
  /** Pre-loaded transcript turns to display (empty array = fresh conversation). */
  initialMessages?: Message[];
  /** Called after the first assistant answer so the sidebar can refresh its list. */
  onConversationListChange?: (conversations: ConversationSummary[]) => void;
}

/** Fetches the updated conversation list via BFF and calls the callback if it succeeds. */
async function refreshConversations(
  onConversationListChange: ((conversations: ConversationSummary[]) => void) | undefined
): Promise<void> {
  if (!onConversationListChange) return;
  try {
    const res = await fetch(CONVERSATIONS_PATH);
    if (!res.ok) return;
    const body = (await res.json()) as { conversations?: ConversationSummary[] };
    if (Array.isArray(body.conversations)) {
      onConversationListChange(body.conversations);
    }
  } catch {
    // A09: never log. Sidebar refresh is best-effort; chat continues.
  }
}

export function ChatForm({
  initialMessages = [],
  onConversationListChange,
}: ChatFormProps) {
  const [question, setQuestion] = useState("");
  // AC CHAT-004: initialMessages is the useState initializer only.
  // Conversation switches are handled by key-based remount in ChatShell
  // (key={selectedId ?? "new"}), so this component is always freshly
  // mounted with the correct transcript — no useEffect reset needed.
  const [messages, setMessages] = useState<Message[]>(initialMessages);
  const [streamingText, setStreamingText] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleSubmit = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const query = question.trim();
      if (!query || isStreaming) return;

      setErrorMessage(null);
      // AC: optimistic echo — user message appears immediately on send.
      setMessages((prev) => [...prev, { role: "user", text: query }]);
      setQuestion("");
      setStreamingText("");
      setIsStreaming(true);

      const controller = new AbortController();

      try {
        const response = await fetch(CHAT_STREAM_PATH, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query }),
          signal: controller.signal,
        });

        if (!response.ok || response.body === null) {
          setStreamingText(null);
          setIsStreaming(false);
          setErrorMessage(ERROR_MESSAGE_FRENCH);
          return;
        }

        let accumulated = "";
        let streamFailed = false;
        // CHAT-003: capture mode from the meta chunk and citations from done.
        let capturedMode: string | undefined;
        let capturedCitations: unknown[] | undefined;
        for await (const chunk of consumeSseStream(response.body)) {
          if (chunk.kind === "meta") {
            capturedMode = chunk.mode || undefined;
          } else if (chunk.kind === "token") {
            accumulated += chunk.text;
            setStreamingText(accumulated);
          } else if (chunk.kind === "stream-error") {
            streamFailed = true;
            break;
          } else if (chunk.kind === "done") {
            capturedCitations = chunk.citations.length > 0 ? chunk.citations : undefined;
            break;
          }
        }

        setStreamingText(null);
        setIsStreaming(false);

        if (streamFailed) {
          setErrorMessage(ERROR_MESSAGE_FRENCH);
          return;
        }

        const committedText = accumulated || ERROR_MESSAGE_FRENCH;
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            text: committedText,
            mode: capturedMode,
            citations: capturedCitations,
          },
        ]);

        // AC CHAT-004: refresh sidebar list after first assistant answer.
        // Best-effort: failure here does not affect the chat thread.
        await refreshConversations(onConversationListChange);
      } catch {
        // Network failure or AbortError — never log (may contain query context).
        setStreamingText(null);
        setIsStreaming(false);
        setErrorMessage(ERROR_MESSAGE_FRENCH);
      }
    },
    [question, isStreaming, onConversationListChange]
  );

  const hasFirstToken = isStreaming && streamingText !== "";

  return (
    <section className={styles.page}>
      <h1 className={styles.heading}>Chat — Archives de Nocilia</h1>

      <div className={styles.thread}>
        {messages.map((message, index) =>
          message.role === "user" ? (
            <p key={index} className={styles.messageUser}>
              {message.text}
            </p>
          ) : (
            // CHAT-003: committed assistant answers render as sanitized Markdown.
            // data-testid="assistant-answer" lives on AssistantAnswer's container div.
            <div key={index} className={styles.messageAssistant}>
              <AssistantAnswer
                text={message.text}
                mode={message.mode}
                citations={message.citations}
              />
            </div>
          )
        )}

        {/* AC: streaming indicator shows until the first token; then token text renders. */}
        {isStreaming && (
          <p
            className={styles.messageAssistant}
            data-testid="streaming-answer"
            aria-live="polite"
            aria-label="Réponse en cours"
          >
            {hasFirstToken ? (
              streamingText
            ) : (
              <span
                className={styles.streamingIndicator}
                aria-hidden="true"
              />
            )}
          </p>
        )}

        {errorMessage !== null && (
          <p className={styles.errorMessage} role="alert">
            {errorMessage}
          </p>
        )}
      </div>

      {/* AC: send control disabled while a response streams (no double-submit) */}
      <form className={styles.form} onSubmit={handleSubmit}>
        <textarea
          name="question"
          aria-label="Votre question"
          className={styles.textarea}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          disabled={isStreaming}
          rows={3}
          placeholder="Posez votre question sur le lore de Nocilia…"
        />
        <button
          type="submit"
          className={styles.submitButton}
          disabled={isStreaming || question.trim() === ""}
        >
          {isStreaming ? "Réponse en cours…" : "Envoyer"}
        </button>
      </form>
    </section>
  );
}
