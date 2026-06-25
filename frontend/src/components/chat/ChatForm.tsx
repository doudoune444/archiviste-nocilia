"use client";
/**
 * ChatForm — streaming chat input/output (CHAT-002 + CHAT-003 + CHAT-004 + #249).
 *
 * Manages form state, optimistic user message echo, and incremental assistant
 * answer rendering via the SSE consumer.
 *
 * #249 additions (Gemini/Mistral-style surface):
 * - Welcome state (empty thread): a short title, a vertically centered input,
 *   and four hardcoded suggestion chips. Clicking a chip sends that exact
 *   question immediately (same path as form submit) and switches to the
 *   conversation state.
 * - Conversation state (≥1 message OR a stream in progress OR a loaded
 *   transcript): the input anchors to the bottom and the thread fills the space
 *   above with scroll, auto-scrolling to the last message during an exchange.
 * - Modern input: auto-growing textarea; Enter submits; Shift+Enter inserts a
 *   newline; the send control is an icon integrated into the field. The existing
 *   anti-double-submit guard (disabled during streaming) is preserved.
 *
 * AC-scope (CHAT-002): token-by-token rendering, streaming indicator, optimistic
 * echo, double-submit guard, single French error message on failure.
 * AC-scope (CHAT-003): committed assistant answers rendered as sanitized Markdown;
 *   meta.mode and done.citations captured and surfaced in AssistantAnswer.
 * AC-scope (CHAT-005): per-answer SignalForm rendered under each committed assistant
 *   answer when conversationId is available from the meta SSE chunk.
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

/** Welcome title shown on the empty thread (#249). */
const WELCOME_TITLE = "Bienvenue aux archives de Nocilia";

/** Hardcoded suggestion chips (#249) — sent verbatim on click, no configuration. */
const SUGGESTION_CHIPS = [
  "Qui est Blowen ?",
  "Qu'est-ce que le Cérafon ?",
  "Qui a élu domicile dans les ruines de Periste ?",
  "Combien font 2+2 ?",
] as const;

/** Turn-header identity for assistant turns (#326), per chat-1-filet.html. */
const ASSISTANT_AVATAR = "🪶";
const ASSISTANT_LABEL = "Archiviste";

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
  /**
   * #291: conversation id of a resumed conversation, or undefined for a fresh
   * one. Source of truth for the id sent in each message body; for a fresh
   * conversation it is captured server-side from the first meta SSE chunk.
   */
  initialConversationId?: string;
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
  initialConversationId,
  onConversationListChange,
}: ChatFormProps) {
  const [question, setQuestion] = useState("");
  // #291: single source of truth for the conversation id. Seeded from a resumed
  // conversation, else captured from the first meta SSE chunk (server-generated).
  const [currentConversationId, setCurrentConversationId] = useState<
    string | undefined
  >(initialConversationId);
  // AC CHAT-004: initialMessages is the useState initializer only.
  // Conversation switches are handled by key-based remount in ChatShell
  // (key={selectedId ?? "new"}), so this component is always freshly
  // mounted with the correct transcript — no useEffect reset needed.
  const [messages, setMessages] = useState<Message[]>(initialMessages);
  const [streamingText, setStreamingText] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const threadEndRef = useRef<HTMLDivElement | null>(null);

  // #249: conversation state once a turn exists, a stream is live, or a
  // transcript was loaded on mount. Welcome state only for a truly empty thread.
  const hasConversation = messages.length > 0 || isStreaming;

  const consumeStreamIntoThread = useCallback(
    async (body: ReadableStream<Uint8Array>) => {
      let accumulated = "";
      let streamFailed = false;
      let capturedMode: string | undefined;
      let capturedCitations: unknown[] | undefined;
      let capturedConversationId: string | undefined;

      for await (const chunk of consumeSseStream(body)) {
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

      // #291: persist the server-generated id so every later message belongs to
      // the same conversation. Posed only after the stream-failure check.
      if (capturedConversationId) {
        setCurrentConversationId(capturedConversationId);
      }

      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          text: accumulated || ERROR_MESSAGE_FRENCH,
          mode: capturedMode,
          citations: capturedCitations,
          conversationId: capturedConversationId,
        },
      ]);

      // AC CHAT-004: refresh sidebar list after first assistant answer.
      // Best-effort: failure here does not affect the chat thread.
      await refreshConversations(onConversationListChange);
    },
    [onConversationListChange]
  );

  /** Sends a query through the same path used by the form submit and the chips. */
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
          body: JSON.stringify(
            currentConversationId
              ? { query, conversation_id: currentConversationId }
              : { query }
          ),
          signal: controller.signal,
        });

        if (!response.ok || response.body === null) {
          setStreamingText(null);
          setIsStreaming(false);
          setErrorMessage(ERROR_MESSAGE_FRENCH);
          return;
        }

        await consumeStreamIntoThread(response.body);
      } catch {
        // Network failure or AbortError — never log (may contain query context).
        setStreamingText(null);
        setIsStreaming(false);
        setErrorMessage(ERROR_MESSAGE_FRENCH);
      }
    },
    [isStreaming, consumeStreamIntoThread, currentConversationId]
  );

  const handleSubmit = useCallback(
    (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      void sendQuery(question);
    },
    [question, sendQuery]
  );

  // #249: Enter submits; Shift+Enter inserts a newline (default textarea behavior).
  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        void sendQuery(question);
      }
    },
    [question, sendQuery]
  );

  // #249: auto-scroll to the latest message during an exchange.
  // scrollIntoView is absent in jsdom (unit tests) — guard the call.
  useEffect(() => {
    if (!hasConversation) return;
    const node = threadEndRef.current;
    if (typeof node?.scrollIntoView === "function") {
      node.scrollIntoView({ block: "end" });
    }
  }, [messages, streamingText, hasConversation]);

  const hasFirstToken = isStreaming && streamingText !== "";
  const formState = hasConversation ? "conversation" : "welcome";

  return (
    <section className={styles.page} data-state={formState}>
      {hasConversation ? (
        <div className={styles.thread}>
          <ThreadMessages messages={messages} />
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
      ) : (
        <div className={styles.welcome}>
          <h1 className={styles.welcomeTitle}>{WELCOME_TITLE}</h1>
        </div>
      )}

      <form className={styles.composer} onSubmit={handleSubmit}>
        <div className={styles.inputRow}>
          <AutoGrowTextarea
            value={question}
            disabled={isStreaming}
            onChange={setQuestion}
            onKeyDown={handleKeyDown}
          />
          <button
            type="submit"
            className={styles.sendButton}
            disabled={isStreaming || question.trim() === ""}
            aria-label="Envoyer"
            title="Envoyer"
          >
            <SendIcon />
          </button>
        </div>
        {!hasConversation && (
          <ul className={styles.chips} aria-label="Suggestions de questions">
            {SUGGESTION_CHIPS.map((chip) => (
              <li key={chip}>
                <button
                  type="button"
                  className={styles.chip}
                  disabled={isStreaming}
                  onClick={() => void sendQuery(chip)}
                >
                  {chip}
                </button>
              </li>
            ))}
          </ul>
        )}
      </form>
    </section>
  );
}

interface ThreadMessagesProps {
  messages: Message[];
}

function ThreadMessages({ messages }: ThreadMessagesProps) {
  return (
    <>
      {messages.map((message, index) =>
        message.role === "user" ? (
          <p key={index} className={styles.messageUser}>
            {message.text}
          </p>
        ) : (
          <AssistantTurn key={index} message={message} />
        )
      )}
    </>
  );
}

/**
 * #326: an assistant turn = a header (🪶 avatar + « Archiviste » label + mode
 * chip) separated by a horizontal rule from the body. The header lives in this
 * layout layer; AssistantAnswer remains responsible for the body only.
 */
function AssistantTurn({ message }: { message: Message }) {
  return (
    <div className={styles.messageAssistant}>
      <header className={styles.turnHeader} data-testid="turn-header">
        <span className={styles.roleAvatar} aria-hidden="true">
          {ASSISTANT_AVATAR}
        </span>
        <span className={styles.roleLabel}>
          {ASSISTANT_LABEL}
          {message.mode !== undefined && (
            <span data-testid="mode-chip" className={styles.modeChip}>
              {message.mode}
            </span>
          )}
        </span>
      </header>
      <hr className={styles.turnRule} />
      {/* CHAT-003: committed assistant answers render as sanitized Markdown.
          data-testid="assistant-answer" lives on AssistantAnswer's container. */}
      <AssistantAnswer text={message.text} citations={message.citations} />
      {message.conversationId !== undefined && (
        <SignalForm
          conversationId={message.conversationId}
          citations={message.citations}
        />
      )}
    </div>
  );
}

interface AutoGrowTextareaProps {
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
  onKeyDown: (event: React.KeyboardEvent<HTMLTextAreaElement>) => void;
}

/** Auto-growing textarea: height follows content up to a CSS max-height (#249). */
function AutoGrowTextarea({
  value,
  disabled,
  onChange,
  onKeyDown,
}: AutoGrowTextareaProps) {
  return (
    <textarea
      name="question"
      aria-label="Votre question"
      className={styles.textarea}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      onKeyDown={onKeyDown}
      disabled={disabled}
      rows={1}
      placeholder="Posez votre question sur le lore de Nocilia…"
    />
  );
}

/** Integrated send icon (#249) — decorative; the button carries the aria-label. */
function SendIcon() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <line x1="22" y1="2" x2="11" y2="13" />
      <polygon points="22 2 15 22 11 13 2 9 22 2" />
    </svg>
  );
}
