"use client";
/**
 * /chat — streaming chat surface (CHAT-002).
 *
 * Client Component: manages form state, optimistic user message echo, and
 * incremental assistant answer rendering via the SSE consumer.
 *
 * AC-scope: token-by-token rendering, streaming indicator, optimistic echo,
 * double-submit guard, single French error message on failure.
 * OUT OF SCOPE: Markdown rendering, history — downstream slices.
 *
 * CHAT-005: per-answer SignalForm rendered under each committed assistant
 * answer when conversationId is available from the meta SSE chunk.
 *
 * A09: query text is never logged.
 * A03: LLM output is rendered as plain text (pre-wrap) — never dangerouslySetInnerHTML.
 */

import { useState, useCallback, useRef } from "react";
import { consumeSseStream } from "@/lib/sse-stream";
import { SignalForm } from "@/components/signal-form/SignalForm";
import styles from "./chat.module.css";

/** Named constant for the gateway path — keeps the module self-documenting. */
const CHAT_STREAM_PATH = "/api/v1/chat/stream";

/** French error message shown on any network or backend failure. */
const ERROR_MESSAGE_FRENCH =
  "Une erreur est survenue. Veuillez réessayer dans quelques instants.";

/**
 * Shared contract for a chat message.
 * All optional fields beyond role/text are filled by specific tickets;
 * keep all optional so parallel slices compose without conflicts.
 */
interface Message {
  role: "user" | "assistant";
  text: string;
  mode?: string;            // another ticket
  citations?: unknown[];    // another ticket
  conversationId?: string;  // CHAT-005: set from meta.conversation_id
}

function ChatForm() {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [streamingText, setStreamingText] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  // Ref keeps the AbortController for the current request so the component
  // can cancel if it unmounts (not used for user-facing cancel UI yet).
  const abortRef = useRef<AbortController | null>(null);

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
      abortRef.current = controller;

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
        // CHAT-005: capture conversation_id from the meta chunk so we can
        // attach it to the committed assistant message for SignalForm.
        let capturedConversationId: string | undefined;

        for await (const chunk of consumeSseStream(response.body)) {
          if (chunk.kind === "meta") {
            // meta.conversation_id is always a string (may be empty on error path).
            if (chunk.conversation_id) {
              capturedConversationId = chunk.conversation_id;
            }
          } else if (chunk.kind === "token") {
            accumulated += chunk.text;
            setStreamingText(accumulated);
          } else if (chunk.kind === "stream-error") {
            // AC: a network/backend failure shows a single clear error message.
            streamFailed = true;
            break;
          } else if (chunk.kind === "done") {
            break;
          }
        }

        setStreamingText(null);
        setIsStreaming(false);

        if (streamFailed) {
          setErrorMessage(ERROR_MESSAGE_FRENCH);
          return;
        }

        // Commit the finished answer to the message thread.
        // Fallback message ensures the user never sees their question with no reply.
        const committedText = accumulated || ERROR_MESSAGE_FRENCH;
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            text: committedText,
            conversationId: capturedConversationId,
          },
        ]);
      } catch {
        // Network failure or AbortError — never log (may contain query context).
        setStreamingText(null);
        setIsStreaming(false);
        setErrorMessage(ERROR_MESSAGE_FRENCH);
      }
    },
    [question, isStreaming]
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
            <div key={index}>
              <p
                className={styles.messageAssistant}
                data-testid="assistant-answer"
              >
                {message.text}
              </p>
              {/* AC CHAT-005: render the signal control under each committed
                  assistant answer when the conversationId is available. */}
              {message.conversationId !== undefined && (
                <SignalForm
                  conversationId={message.conversationId}
                  citations={message.citations}
                />
              )}
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

/**
 * /chat page — App Router page component.
 * No Suspense needed here (no useSearchParams or similar suspended hooks).
 */
export default function ChatPage() {
  return <ChatForm />;
}
