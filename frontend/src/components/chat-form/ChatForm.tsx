"use client";
/**
 * ChatForm — streaming chat input/output (CHAT-002/003/004/005 + #245 refonte).
 *
 * Manages form state, optimistic user message echo, and incremental assistant
 * answer rendering via the SSE consumer.
 *
 * #245 refonte (Gemini/Mistral style):
 * - Welcome state (empty thread): short welcome heading + centered input + four
 *   suggestion chips. Once an exchange begins (≥ 1 message OR streaming OR a
 *   loaded transcript) the input anchors to the bottom and the thread scrolls
 *   above it with auto-scroll to the latest message.
 * - A chip click sends its question immediately (same path as form submit).
 * - Keyboard: Enter submits, Shift+Enter inserts a newline.
 * - The send control is an icon button embedded in the field.
 *
 * Invariants kept:
 * - Accepts `initialMessages` to render a loaded transcript on mount (no re-render loop).
 * - Calls `onConversationListChange` after the first message so the sidebar refreshes.
 * - Double-submit guard: send disabled while streaming.
 *
 * A09: query text is never logged.
 * A03: LLM output rendered via AssistantAnswer (react-markdown + rehype-sanitize).
 *      Plain streaming text uses pre-wrap — never dangerouslySetInnerHTML.
 */

import { useState, useCallback, useEffect, useRef } from "react";
import { consumeSseStream } from "@/lib/sse-stream";
import AssistantAnswer from "@/components/assistant-answer/AssistantAnswer";
import { SignalForm } from "@/components/signal-form/SignalForm";
import type {
  ConversationSummary,
  Message,
} from "@/components/conversation-history/types";
import styles from "./chat.module.css";

const CHAT_STREAM_PATH = "/api/v1/chat/stream";
const CONVERSATIONS_PATH = "/api/v1/conversations";

const WELCOME_HEADING = "Bienvenue aux archives de Nocilia";

/** French error message shown on any network or backend failure. */
const ERROR_MESSAGE_FRENCH =
  "Une erreur est survenue. Veuillez réessayer dans quelques instants.";

/**
 * Four hard-coded suggestion questions (#245). No configuration — a chip click
 * submits the question immediately through the same path as the form.
 */
const SUGGESTION_CHIPS: readonly string[] = [
  "Qui est Blowen ?",
  "Qu'est-ce que le Cérafon ?",
  "Qui a élu domicile dans les ruines de Periste ?",
  "Combien font 2+2 ?",
];

export type { Message };

interface ChatFormProps {
  /** Pre-loaded transcript turns to display (empty array = fresh conversation). */
  initialMessages?: Message[];
  /** Called after the first assistant answer so the sidebar can refresh its list. */
  onConversationListChange?: (conversations: ConversationSummary[]) => void;
}

/** Fetches the updated conversation list via BFF and calls the callback if it succeeds. */
async function refreshConversations(
  onConversationListChange:
    | ((conversations: ConversationSummary[]) => void)
    | undefined
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
  // initialMessages is the useState initializer only; conversation switches are
  // handled by key-based remount in the shell (key={selectedId ?? "new"}).
  const [messages, setMessages] = useState<Message[]>(initialMessages);
  const [streamingText, setStreamingText] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const threadEndRef = useRef<HTMLDivElement | null>(null);

  // #245: welcome state until the thread has any content. A loaded transcript
  // (initialMessages non-empty) starts directly in conversation state.
  const hasConversation = messages.length > 0 || isStreaming;

  // AC #245: auto-scroll to the latest message during an exchange.
  // scrollIntoView is absent in jsdom (unit tests) — guard with typeof.
  useEffect(() => {
    const end = threadEndRef.current;
    if (end && typeof end.scrollIntoView === "function") {
      end.scrollIntoView({ block: "end" });
    }
  }, [messages, streamingText]);

  const sendQuery = useCallback(
    async (rawQuery: string) => {
      const query = rawQuery.trim();
      if (!query || isStreaming) return;

      setErrorMessage(null);
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
        let capturedMode: string | undefined;
        let capturedCitations: unknown[] | undefined;
        let capturedConversationId: string | undefined;
        for await (const chunk of consumeSseStream(response.body)) {
          if (chunk.kind === "meta") {
            capturedMode = chunk.mode || undefined;
            if (chunk.conversation_id) {
              capturedConversationId = chunk.conversation_id;
            }
          } else if (chunk.kind === "token") {
            accumulated += chunk.text;
            setStreamingText(accumulated);
          } else if (chunk.kind === "stream-error") {
            streamFailed = true;
            break;
          } else if (chunk.kind === "done") {
            capturedCitations =
              chunk.citations.length > 0 ? chunk.citations : undefined;
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
            conversationId: capturedConversationId,
          },
        ]);

        await refreshConversations(onConversationListChange);
      } catch {
        // Network failure or AbortError — never log (may contain query context).
        setStreamingText(null);
        setIsStreaming(false);
        setErrorMessage(ERROR_MESSAGE_FRENCH);
      }
    },
    [isStreaming, onConversationListChange]
  );

  const handleSubmit = useCallback(
    (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      void sendQuery(question);
    },
    [question, sendQuery]
  );

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
      // AC #245: Enter submits; Shift+Enter inserts a newline (default behavior).
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        void sendQuery(question);
      }
    },
    [question, sendQuery]
  );

  const hasFirstToken = isStreaming && streamingText !== "";
  const isSendDisabled = isStreaming || question.trim() === "";

  return (
    <section
      className={hasConversation ? styles.pageConversation : styles.pageWelcome}
      data-state={hasConversation ? "conversation" : "welcome"}
    >
      {!hasConversation && (
        <div className={styles.welcome}>
          <h1 className={styles.welcomeHeading}>{WELCOME_HEADING}</h1>
        </div>
      )}

      {hasConversation && (
        <div className={styles.thread}>
          {messages.map((message, index) =>
            message.role === "user" ? (
              <p key={index} className={styles.messageUser}>
                {message.text}
              </p>
            ) : (
              <div key={index} className={styles.messageAssistant}>
                <AssistantAnswer
                  text={message.text}
                  mode={message.mode}
                  citations={message.citations}
                />
                {message.conversationId !== undefined && (
                  <SignalForm
                    conversationId={message.conversationId}
                    citations={message.citations}
                  />
                )}
              </div>
            )
          )}

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
                <span className={styles.streamingIndicator} aria-hidden="true" />
              )}
            </p>
          )}

          {errorMessage !== null && (
            <p className={styles.errorMessage} role="alert">
              {errorMessage}
            </p>
          )}
          <div ref={threadEndRef} />
        </div>
      )}

      <div className={styles.composer}>
        {!hasConversation && errorMessage !== null && (
          <p className={styles.errorMessage} role="alert">
            {errorMessage}
          </p>
        )}
        {/* AC #245: send control is an icon button embedded in the field. */}
        <form className={styles.form} onSubmit={handleSubmit}>
          <div className={styles.inputRow}>
            <textarea
              name="question"
              aria-label="Votre question"
              className={styles.textarea}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={handleKeyDown}
              disabled={isStreaming}
              rows={1}
              placeholder="Posez votre question sur le lore de Nocilia…"
            />
            <button
              type="submit"
              className={styles.submitButton}
              disabled={isSendDisabled}
              aria-label="Envoyer"
            >
              <span aria-hidden="true" className={styles.sendIcon}>
                ↑
              </span>
            </button>
          </div>
        </form>

        {!hasConversation && (
          <div className={styles.chips} aria-label="Suggestions de questions">
            {SUGGESTION_CHIPS.map((chip) => (
              <button
                key={chip}
                type="button"
                className={styles.chip}
                onClick={() => void sendQuery(chip)}
                disabled={isStreaming}
              >
                {chip}
              </button>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
