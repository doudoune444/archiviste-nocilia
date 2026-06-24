"use client";
/**
 * ChatShell — client wrapper that drives the chat thread (CHAT-004, #248).
 *
 * Receives the initial conversation list as a prop (populated server-side by the page RSC)
 * and manages which conversation is currently open in the chat thread.
 *
 * #248: the conversation history list and the "Nouvelle conversation" reset are
 * registered into the global sidebar app-shell via useRegisterChatSidebar; the
 * main area renders only the chat thread.
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

import { useState, useCallback, useMemo } from "react";
import { ConversationHistory } from "./ConversationHistory";
import { mapTranscriptToMessages } from "./transcript";
import {
  isConversationList,
  isConversationMessages,
  type ConversationSummary,
  type Message,
} from "./types";
import { ChatForm } from "@/components/chat/ChatForm";
import { ConfirmDialog } from "@/components/confirm-dialog/ConfirmDialog";
import { useRegisterChatSidebar } from "@/components/app-sidebar/SidebarChatContext";
import styles from "./ChatShell.module.css";

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

const CONVERSATIONS_PATH = "/api/v1/conversations";
const DELETE_DIALOG_TITLE = "Supprimer cette conversation ?";
const DELETE_DANGER_LABEL = "Supprimer";
const DELETE_CONFLICT_MESSAGE =
  "Suppression impossible : un signalement est en cours sur cette conversation.";
const DELETE_GENERIC_ERROR =
  "La suppression a échoué. Veuillez réessayer.";

const HTTP_CONFLICT = 409;

/** Body of the delete confirmation modal, naming the target conversation. */
function deleteDialogMessage(title: string): string {
  return `Cette action est définitive. L'historique de la conversation "${title}" sera supprimé et ne pourra pas être récupéré.`;
}

/**
 * Re-fetches the owner's conversation list and reconciles local state.
 * #287: the deleted row disappears only here — after the server confirmed the
 * DELETE — so a conversation can never "resurrect" via optimistic removal.
 * Best-effort: a failed refresh leaves the list untouched (no throw).
 */
async function reconcileConversations(
  setConversations: (conversations: ConversationSummary[]) => void
): Promise<void> {
  try {
    const response = await fetch(CONVERSATIONS_PATH);
    if (!response.ok) return;
    const body: unknown = await response.json();
    if (isConversationList(body)) {
      setConversations(body.conversations);
    }
  } catch {
    // A09: never log. Reconcile is best-effort.
  }
}

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
  // #287: id pending confirmation (modal open) and id whose DELETE is in flight.
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

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

  // #287: trash click → open the confirmation modal naming the conversation.
  const handleRequestDelete = useCallback((id: string) => {
    setDeleteError(null);
    setPendingDeleteId(id);
  }, []);

  const handleCancelDelete = useCallback(() => {
    setPendingDeleteId(null);
  }, []);

  const handleConfirmDelete = useCallback(async () => {
    if (pendingDeleteId === null) return;
    const id = pendingDeleteId;
    setPendingDeleteId(null);
    setDeleteError(null);
    setDeletingId(id);
    try {
      const response = await fetch(`${CONVERSATIONS_PATH}/${id}`, {
        method: "DELETE",
      });
      if (response.ok) {
        await reconcileConversations(setConversations);
        // AC: deleting the open conversation returns to "Nouvelle conversation";
        // deleting any other conversation leaves the open thread untouched.
        if (selectedId === id) {
          setSelectedId(null);
          setLoadedMessages(null);
          setTranscriptError(null);
        }
        return;
      }
      // AC: never optimistic — the item stays; surface why deletion failed.
      setDeleteError(
        response.status === HTTP_CONFLICT
          ? DELETE_CONFLICT_MESSAGE
          : DELETE_GENERIC_ERROR
      );
    } catch {
      // A09: never log response content.
      setDeleteError(DELETE_GENERIC_ERROR);
    } finally {
      setDeletingId(null);
    }
  }, [pendingDeleteId, selectedId]);

  const pendingConversation = useMemo(
    () => conversations.find((conv) => conv.id === pendingDeleteId) ?? null,
    [conversations, pendingDeleteId]
  );

  // #248: inject the history list + reset handler into the global sidebar.
  // The history element is memoized so the registration effect only re-fires
  // when the data it renders actually changes (not on every render).
  const history = useMemo(
    () => (
      <ConversationHistory
        conversations={conversations}
        selectedId={selectedId}
        onSelect={handleSelectConversation}
        onRequestDelete={handleRequestDelete}
        deletingId={deletingId}
      />
    ),
    [
      conversations,
      selectedId,
      handleSelectConversation,
      handleRequestDelete,
      deletingId,
    ]
  );
  useRegisterChatSidebar({ history, onNewConversation: handleNew });

  return (
    <div className={styles.shell}>
      {transcriptError !== null && (
        <p role="alert" className={styles.transcriptError}>
          {transcriptError}
        </p>
      )}
      {deleteError !== null && (
        <p role="alert" className={styles.deleteError}>
          {deleteError}
        </p>
      )}
      {pendingConversation !== null && (
        <ConfirmDialog
          title={DELETE_DIALOG_TITLE}
          message={deleteDialogMessage(pendingConversation.title)}
          dangerLabel={DELETE_DANGER_LABEL}
          onConfirm={handleConfirmDelete}
          onCancel={handleCancelDelete}
        />
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
        initialConversationId={selectedId ?? undefined}
        onConversationListChange={handleConversationStarted}
      />
    </div>
  );
}
