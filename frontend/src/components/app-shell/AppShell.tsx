"use client";
/**
 * AppShell — the global Mistral-style layout (#245).
 *
 * A fixed left sidebar is present on every page. Top: the brand button that
 * opens the navigation popover. Then "Nouvelle conversation". Then the
 * conversation history — ONLY on the Archiviste page (/). Bottom: the account
 * block. The old top nav bar and global footer are gone.
 *
 * On the chat route (/) the shell renders the chat surface itself and owns the
 * thread state (selected conversation, loaded transcript) so the sidebar history
 * and the thread stay in sync. On every other route it renders the page
 * children. "Nouvelle conversation" resets the thread on /, or navigates to /
 * from elsewhere.
 *
 * Mobile (<600px): the sidebar is an overlay drawer toggled by a hamburger.
 *
 * Reload returns to an empty thread: the shell never auto-loads a conversation,
 * it only pre-populates the sidebar list (no localStorage).
 *
 * A01: identity is the cookie forwarded by bff-proxy, never a client value.
 * A09: transcript content is never logged.
 */

import { useState, useCallback } from "react";
import { usePathname, useRouter } from "next/navigation";
import { NavPopover, AccountBlock, type Tier } from "./SidebarNav";
import { ConversationHistory } from "@/components/conversation-history/ConversationHistory";
import { mapTranscriptToMessages } from "@/components/conversation-history/transcript";
import {
  isConversationMessages,
  type ConversationSummary,
  type Message,
} from "@/components/conversation-history/types";
import { ChatForm } from "@/components/chat-form/ChatForm";
import styles from "./AppShell.module.css";

const CHAT_ROUTE = "/";

const EMPTY_MESSAGES: Message[] = [];

const TRANSCRIPT_LOAD_ERROR =
  "Impossible de charger la conversation. Veuillez réessayer.";

const LABEL_NEW_CONVERSATION = "Nouvelle conversation";

interface AppShellProps {
  tier: Tier;
  email: string | null;
  initialConversations: ConversationSummary[];
  children: React.ReactNode;
}

export function AppShell({
  tier,
  email,
  initialConversations,
  children,
}: AppShellProps) {
  const pathname = usePathname();
  const router = useRouter();
  const isChatRoute = pathname === CHAT_ROUTE;

  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const [conversations, setConversations] =
    useState<ConversationSummary[]>(initialConversations);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loadedMessages, setLoadedMessages] = useState<Message[] | null>(null);
  const [transcriptError, setTranscriptError] = useState<string | null>(null);

  const closeDrawer = useCallback(() => setIsDrawerOpen(false), []);

  const handleSelectConversation = useCallback(async (id: string) => {
    setTranscriptError(null);
    setSelectedId(id);
    setIsDrawerOpen(false);
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
      setLoadedMessages(mapTranscriptToMessages(body.messages));
    } catch {
      // A09: never log response content.
      setTranscriptError(TRANSCRIPT_LOAD_ERROR);
    }
  }, []);

  const handleNew = useCallback(() => {
    setIsDrawerOpen(false);
    if (!isChatRoute) {
      // AC #245: "Nouvelle conversation" from another page returns to / fresh.
      router.push(CHAT_ROUTE);
      return;
    }
    setSelectedId(null);
    setLoadedMessages(null);
    setTranscriptError(null);
  }, [isChatRoute, router]);

  const handleConversationListChange = useCallback(
    (next: ConversationSummary[]) => setConversations(next),
    []
  );

  return (
    <div className={styles.shell}>
      <button
        type="button"
        className={styles.hamburger}
        aria-label="Ouvrir le menu"
        aria-expanded={isDrawerOpen}
        onClick={() => setIsDrawerOpen((open) => !open)}
      >
        ☰
      </button>
      <div
        className={isDrawerOpen ? styles.backdropOpen : styles.backdrop}
        onClick={closeDrawer}
        aria-hidden="true"
      />

      <aside
        className={`${styles.sidebar} ${
          isDrawerOpen ? styles.sidebarOpen : ""
        }`}
      >
        <NavPopover tier={tier} />

        <div className={styles.middle}>
          <button
            type="button"
            className={styles.newButton}
            onClick={handleNew}
            data-testid="new-conversation-btn"
          >
            {LABEL_NEW_CONVERSATION}
          </button>

          {isChatRoute && (
            <ConversationHistory
              conversations={conversations}
              selectedId={selectedId}
              onSelect={handleSelectConversation}
            />
          )}
        </div>

        <AccountBlock tier={tier} email={email} />
      </aside>

      <main className={styles.content}>
        {isChatRoute ? (
          <>
            {transcriptError !== null && (
              <p role="alert" className={styles.transcriptError}>
                {transcriptError}
              </p>
            )}
            <ChatForm
              key={selectedId ?? "new"}
              initialMessages={loadedMessages ?? EMPTY_MESSAGES}
              onConversationListChange={handleConversationListChange}
            />
          </>
        ) : (
          children
        )}
      </main>
    </div>
  );
}
