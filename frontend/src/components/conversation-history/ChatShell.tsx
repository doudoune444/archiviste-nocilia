"use client";
/**
 * ChatShell — client wrapper that composes ConversationHistory sidebar + ChatForm (CHAT-004).
 *
 * Receives the initial conversation list as a prop (populated server-side by the page RSC)
 * and manages which conversation is currently open in the chat thread.
 *
 * "Stays cleared on reload" guarantee (AC):
 *   Default state = empty thread (no selectedId, no messages loaded on mount).
 *   No localStorage is used. A page reload returns to the default empty state
 *   because server-side rendering never auto-loads a conversation — it only
 *   pre-populates the sidebar list.
 *
 * "No phantom empty conversation" guarantee (AC):
 *   The thread starts empty and stays empty until the user types the first message
 *   OR clicks a past conversation. No conversation row is created on mount.
 *
 * A01: identity never supplied by the client — bff-proxy forwards the cookie.
 * A09: transcript content is never logged.
 */

import { useState, useCallback } from "react";
import { ConversationHistory } from "./ConversationHistory";
import { mapTranscriptToMessages } from "./transcript";
import {
  isConversationMessages,
  type ConversationSummary,
  type Message,
} from "./types";
import { ChatForm } from "@/components/chat/ChatForm";

/**
 * Stable empty array reference — passed to ChatForm when no conversation is selected.
 * WHY: a new `[]` literal on every render would trigger ChatForm's useState
 * initializer (on each key-based remount) with a different identity on every
 * render, but more importantly it avoids any accidental effect dependency
 * re-fires if the pattern were ever reverted. Using a module-level const is the
 * idiomatic way to guarantee referential stability.
 */
const EMPTY_MESSAGES: Message[] = [];

/** French error label shown when a transcript cannot be loaded. */
const TRANSCRIPT_LOAD_ERROR =
  "Impossible de charger la conversation. Veuillez réessayer.";

interface ChatShellProps {
  initialConversations: ConversationSummary[];
}

export function ChatShell({ initialConversations }: ChatShellProps) {
  const [conversations, setConversations] =
    useState<ConversationSummary[]>(initialConversations);
  // AC: default = no selected conversation (empty thread on load and on "Nouvelle conversation")
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loadedMessages, setLoadedMessages] = useState<Message[] | null>(null);
  const [transcriptError, setTranscriptError] = useState<string | null>(null);

  const handleSelectConversation = useCallback(async (id: string) => {
    setTranscriptError(null);
    setSelectedId(id);

    try {
      const response = await fetch(`/api/v1/conversations/${id}/messages`);
      if (!response.ok) {
        setTranscriptError(TRANSCRIPT_LOAD_ERROR);
        return;
      }
      const body: unknown = await response.json();
      if (!isConversationMessages(body)) {
        setTranscriptError(TRANSCRIPT_LOAD_ERROR);
        return;
      }
      // AC: transcript rendered in ordinal order, no phantom messages.
      setLoadedMessages(mapTranscriptToMessages(body.messages));
    } catch {
      // A09: never log response content.
      setTranscriptError(TRANSCRIPT_LOAD_ERROR);
    }
  }, []);

  const handleNew = useCallback(() => {
    // AC: "Nouvelle conversation" clears the view. No localStorage. No reload needed.
    setSelectedId(null);
    setLoadedMessages(null);
    setTranscriptError(null);
  }, []);

  const handleConversationStarted = useCallback(
    (newConversations: ConversationSummary[]) => {
      setConversations(newConversations);
    },
    []
  );

  return (
    <div style={{ display: "flex", minHeight: "100%" }}>
      <ConversationHistory
        conversations={conversations}
        selectedId={selectedId}
        onSelect={handleSelectConversation}
        onNew={handleNew}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        {transcriptError !== null && (
          <p role="alert" style={{ padding: "1rem", color: "var(--color-error-text)" }}>
            {transcriptError}
          </p>
        )}
        {/*
         * key={selectedId ?? "new"} remounts ChatForm on every conversation
         * switch (including switching back to the empty "Nouvelle conversation"
         * state). This is intentional — see B1 fix: remounting is the only
         * safe way to reset stateful children when the identity of the data
         * they own changes. It avoids the useEffect-reset bug where a sidebar
         * refresh caused a new array reference to wipe an in-flight thread.
         */}
        <ChatForm
          key={selectedId ?? "new"}
          initialMessages={loadedMessages ?? EMPTY_MESSAGES}
          onConversationListChange={handleConversationStarted}
        />
      </div>
    </div>
  );
}
