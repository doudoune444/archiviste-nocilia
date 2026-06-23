"use client";
/**
 * ChatForm — streaming chat surface (CHAT-002/003/004/005 + #249 redesign).
 *
 * #249 — Gemini/Mistral-style surface:
 * - Welcome state (empty thread, no stream, no loaded transcript): centered
 *   title + input + four hardcoded suggestion chips. Clicking a chip sends that
 *   question immediately (same path as form submit) and switches to conversation
 *   state.
 * - Conversation state (≥1 message OR a stream in progress OR a loaded
 *   transcript): the input anchors to the bottom, the thread fills the space
 *   above and auto-scrolls to the latest message during an exchange.
 * - Modern input: auto-growing textarea, Enter submits, Shift+Enter inserts a
 *   newline, send is an integrated icon button. The anti-double-submit guard
 *   (disabled during streaming) is preserved.
 *
 * AC-scope (CHAT-002): token-by-token rendering, streaming indicator, optimistic
 * echo, double-submit guard, single French error message on failure.
 * AC-scope (CHAT-003): committed assistant answers rendered as sanitized Markdown;
 *   meta.mode and done.citations captured and surfaced in AssistantAnswer.
 * AC-scope (CHAT-005): per-answer SignalForm under each committed assistant answer.
 * AC-scope (CHAT-004): data-testid="assistant-answer" kept intact.
 *
 * A09: query text is never logged.
 * A03: LLM output rendered via AssistantAnswer (react-markdown + rehype-sanitize).
 *      Plain streaming text uses pre-wrap — never dangerouslySetInnerHTML.
 */

import { useState, useCallback, useRef, useEffect } from "react";
import { consumeSseStream } from "@/lib/sse-stream";
import AssistantAnswer from "@/components/assistant-answer/AssistantAnswer";
import { SignalForm } from "@/components/signal-form/SignalForm";
import type { ConversationSummary } from "@/components/conversation-history/types";
import styles from "./chat.module.css";

const CHAT_STREAM_PATH = "/api/v1/chat/stream";
const CONVERSATIONS_PATH = "/api/v1/conversations";

/** French error message shown on any network or backend failure. */
const ERROR_MESSAGE_FRENCH =
  "Une erreur est survenue. Veuillez réessayer dans quelques instants.";

/** Welcome title shown above the centered input on an empty thread. */
const WELCOME_TITLE = "Bienvenue aux archives de Nocilia";

/**
 * Hardcoded suggestion chips for the welcome state (#249). Clicking one sends
 * the exact text as a query. Order is significant (matches the AC).
 */
export const WELCOME_CHIPS = [
  "Qui est Blowen ?",
  "Qu'est-ce que le Cérafon ?",
  "Qui a élu domicile dans les ruines de Periste ?",
  "Combien font 2+2 ?",
] as const;

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
  // Conversation switches are handled by key-based remount in ChatShell.
  const [messages, setMessages] = useState<Message[]>(initialMessages);
  const [streamingText, setStreamingText] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const threadEndRef = useRef<HTMLDivElement>(null);

  /**
   * Welcome vs conversation state (#249). A loaded transcript seeds `messages`,
   * so a non-empty thread, an optimistic echo, or an in-progress stream all mean
   * "conversation". The welcome state disappears the instant the first message
   * is echoed.
   */
  const isConversationState = messages.length > 0 || isStreaming;

  const sendQuery = useCallback(
    async (rawQuery: string) => {
      const query = rawQuery.trim();
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
            conversationId: capturedConversationId,
          },
        ]);

        // AC CHAT-004: refresh sidebar list after first assistant answer.
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

  // AC #249: Enter submits, Shift+Enter inserts a newline.
  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        void sendQuery(question);
      }
    },
    [question, sendQuery]
  );

  // AC #249: auto-grow the textarea to fit its content.
  const resizeTextarea = useCallback(() => {
    const element = textareaRef.current;
    if (element === null) return;
    element.style.height = "auto";
    element.style.height = `${element.scrollHeight}px`;
  }, []);

  useEffect(() => {
    resizeTextarea();
  }, [question, resizeTextarea]);

  // AC #249: auto-scroll to the latest message during an exchange.
  // scrollIntoView is unavailable in jsdom (and some non-DOM environments);
  // feature-detect rather than assume it exists.
  useEffect(() => {
    const anchor = threadEndRef.current;
    if (anchor && typeof anchor.scrollIntoView === "function") {
      anchor.scrollIntoView({ block: "end" });
    }
  }, [messages, streamingText]);

  const hasFirstToken = isStreaming && streamingText !== "";

  const input = (
    <form className={styles.form} onSubmit={handleSubmit}>
      <div className={styles.inputBar}>
        <textarea
          ref={textareaRef}
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
          disabled={isStreaming || question.trim() === ""}
          aria-label="Envoyer"
          title="Envoyer"
        >
          <SendIcon />
        </button>
      </div>
    </form>
  );

  if (!isConversationState) {
    return (
      <section className={styles.welcome} data-testid="welcome-state">
        <div className={styles.welcomeInner}>
          <h1 className={styles.welcomeHeading}>{WELCOME_TITLE}</h1>
          {input}
          <ul className={styles.chips}>
            {WELCOME_CHIPS.map((chip) => (
              <li key={chip}>
                <button
                  type="button"
                  className={styles.chip}
                  data-testid="suggestion-chip"
                  onClick={() => void sendQuery(chip)}
                >
                  {chip}
                </button>
              </li>
            ))}
          </ul>
        </div>
      </section>
    );
  }

  return (
    <section className={styles.conversation} data-testid="conversation-state">
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

      {input}
    </section>
  );
}

/** Inline send glyph for the integrated icon button (#249). Decorative — the button carries the label. */
function SendIcon() {
  return (
    <svg
      className={styles.sendIcon}
      viewBox="0 0 24 24"
      width="20"
      height="20"
      aria-hidden="true"
      focusable="false"
    >
      <path fill="currentColor" d="M3 11l18-8-8 18-2-7-8-3z" />
    </svg>
  );
}
