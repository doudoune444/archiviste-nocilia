"use client";
/**
 * SidebarChatContext — lets the chat page inject page-specific content into the
 * global sidebar app-shell (#248).
 *
 * The sidebar lives in the root layout and is rendered on every page, but two
 * pieces are chat-page-specific:
 *   - the conversation history list,
 *   - the "Nouvelle conversation" reset handler (resets the open thread).
 *
 * The chat page registers both via useRegisterChatSidebar(); the SidebarShell
 * reads them via useChatSidebar(). On any other page nothing is registered, so
 * the history is hidden and "Nouvelle conversation" falls back to navigation.
 *
 * The setter is exposed through a SEPARATE context from the content so that a
 * component which only registers (the chat page) does not re-render when the
 * content changes — that would otherwise create a render → setContent → render
 * loop with a freshly-built history element on each pass.
 */

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

export interface ChatSidebarContent {
  history: ReactNode;
  onNewConversation: () => void;
}

type SetChatSidebarContent = (content: ChatSidebarContent | null) => void;

const ChatSidebarContentContext = createContext<ChatSidebarContent | null>(null);
const ChatSidebarSetterContext = createContext<SetChatSidebarContent | null>(
  null
);

interface SidebarChatProviderProps {
  children: ReactNode;
}

export function SidebarChatProvider({ children }: SidebarChatProviderProps) {
  const [content, setContent] = useState<ChatSidebarContent | null>(null);
  return (
    <ChatSidebarSetterContext.Provider value={setContent}>
      <ChatSidebarContentContext.Provider value={content}>
        {children}
      </ChatSidebarContentContext.Provider>
    </ChatSidebarSetterContext.Provider>
  );
}

/** Read the currently-registered chat content (null on non-chat pages). */
export function useChatSidebar(): ChatSidebarContent | null {
  return useContext(ChatSidebarContentContext);
}

/** Register chat-page content into the sidebar; clears it on unmount. */
export function useRegisterChatSidebar(content: ChatSidebarContent): void {
  const setContent = useContext(ChatSidebarSetterContext);
  if (setContent === null) {
    throw new Error(
      "useRegisterChatSidebar must be used within a SidebarChatProvider"
    );
  }
  const { history, onNewConversation } = content;
  useEffect(() => {
    setContent({ history, onNewConversation });
    return () => setContent(null);
  }, [setContent, history, onNewConversation]);
}
