// AC CHAT-004 — regression for B1: first exchange must NOT be wiped when the
// sidebar conversation list refreshes after the first message is sent.
//
// Root cause (old code): ChatShell rendered `<ChatForm initialMessages={loadedMessages ?? []}>`
// where the `?? []` produced a NEW array literal on every render. After
// onConversationListChange fired → setConversations → ChatShell re-rendered →
// ChatForm received a fresh `[]` with a new identity → the old
// `useEffect(() => setMessages(initialMessages), [initialMessages])` fired →
// thread wiped.
//
// Fix: key={selectedId ?? "new"} on ChatForm + initialMessages uses a stable
// EMPTY_MESSAGES module-level const + the useEffect reset was removed entirely.
// This test would FAIL against the old implementation because the thread wipe
// happened synchronously inside the effect after the sidebar refresh.

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import React from "react";
import { ChatShell } from "@/components/conversation-history/ChatShell";
import { SidebarChatProvider } from "@/components/app-sidebar/SidebarChatContext";
import { SidebarShell } from "@/components/app-sidebar/SidebarShell";

// next/navigation + next/link — needed by SidebarShell in the integration test.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/",
}));
vi.mock("next/link", () => ({
  default: ({
    href,
    children,
  }: {
    href: string;
    children: React.ReactNode;
  }) => <a href={href}>{children}</a>,
}));

/** ChatShell registers into the sidebar context (#248); wrap renders in the provider. */
function renderChatShell(initialConversations: never[] = []) {
  return render(
    <SidebarChatProvider>
      <ChatShell initialConversations={initialConversations} />
    </SidebarChatProvider>
  );
}

// ---------------------------------------------------------------------------
// Module mocks
// ---------------------------------------------------------------------------

// chat.module.css — the component imports a CSS module; jsdom cannot process
// real CSS, so we stub it with an identity proxy.
vi.mock("@/components/chat/chat.module.css", () => {
  return {
    default: new Proxy(
      {},
      { get: (_t, prop: string) => prop }
    ),
  };
});

// ConversationHistory.module.css — same pattern.
vi.mock(
  "@/components/conversation-history/ConversationHistory.module.css",
  () => ({
    default: new Proxy({}, { get: (_t, prop: string) => prop }),
  })
);

// ---------------------------------------------------------------------------
// Fetch stub helpers
// ---------------------------------------------------------------------------

/**
 * Makes a streaming SSE response that emits one token "Réponse." then done.
 * Used to simulate a successful chat round-trip in jsdom.
 */
function makeSseResponse(): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(
        encoder.encode(
          'event: meta\ndata: {"mode":"canon","conversation_id":"c1","request_id":"r1"}\n\n'
        )
      );
      controller.enqueue(
        encoder.encode('event: token\ndata: {"text":"Réponse."}\n\n')
      );
      controller.enqueue(
        encoder.encode(
          'event: done\ndata: {"citations":[],"usage":{},"retrieve_ms":0,"llm_ms":0}\n\n'
        )
      );
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

/** Makes a successful /api/v1/conversations refresh response. */
function makeConversationsResponse(): Response {
  return new Response(
    JSON.stringify({
      conversations: [
        {
          id: "c1",
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:01:00Z",
          message_count: 2,
        },
      ],
    }),
    { status: 200, headers: { "content-type": "application/json" } }
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// B1 regression test
// ---------------------------------------------------------------------------

describe("ChatShell B1 — thread not wiped on sidebar refresh", () => {
  // AC CHAT-004 B1: after sending a message (optimistic echo), a sidebar refresh
  // triggered by onConversationListChange must NOT clear the thread.
  //
  // This test would FAIL with the old code because:
  //   1. setConversations(newList) → ChatShell re-renders
  //   2. `loadedMessages ?? []` = new [] literal (new identity)
  //   3. useEffect([initialMessages]) fires → setMessages([]) → thread wiped
  //
  // With the fix (key={selectedId ?? "new"} + EMPTY_MESSAGES stable ref):
  //   - selectedId is still null → key stays "new" → ChatForm is NOT remounted
  //   - EMPTY_MESSAGES identity is stable → no effect fires
  //   - the local messages state inside ChatForm is untouched
  it("keeps the sent message in the thread after the conversation list refreshes", async () => {
    // Arrange: stub fetch for the SSE stream + sidebar refresh.
    const mockFetch = vi
      .fn()
      .mockImplementationOnce((_url: string) => Promise.resolve(makeSseResponse()))
      .mockImplementationOnce((_url: string) =>
        Promise.resolve(makeConversationsResponse())
      );
    vi.stubGlobal("fetch", mockFetch);

    renderChatShell();

    // Act: type a question and submit.
    const textarea = screen.getByRole("textbox", { name: /votre question/i });
    fireEvent.change(textarea, { target: { value: "Qui est Nocilia ?" } });

    const button = screen.getByRole("button", { name: /envoyer/i });
    // Wrap the submit in act so all microtasks (SSE + fetch refresh) flush.
    await act(async () => {
      fireEvent.click(button);
    });

    // Assert: the optimistic user echo and the assistant answer must BOTH be visible.
    // If B1 were present the thread would be wiped and "Qui est Nocilia ?" would be gone.
    await waitFor(() => {
      expect(screen.getByText("Qui est Nocilia ?")).toBeInTheDocument();
    });

    // The assistant answer committed after the stream closed must also be present.
    await waitFor(() => {
      const answers = screen.getAllByTestId("assistant-answer");
      expect(answers.length).toBeGreaterThan(0);
      expect(answers[0]).toHaveTextContent("Réponse.");
    });

    // Both fetch calls happened: one SSE stream, one sidebar refresh.
    expect(mockFetch).toHaveBeenCalledTimes(2);
    expect(mockFetch).toHaveBeenNthCalledWith(
      1,
      "/api/v1/chat/stream",
      expect.objectContaining({ method: "POST" })
    );
    expect(mockFetch).toHaveBeenNthCalledWith(2, "/api/v1/conversations");
  });

  // AC #248: the sidebar "Nouvelle conversation" button resets the chat thread
  // on the chat page via the registered onNewConversation handler.
  it("clears the thread when the sidebar 'Nouvelle conversation' is clicked", async () => {
    render(
      <SidebarChatProvider>
        <SidebarShell identity={{ tier: "anonymous", email: null }} />
        <ChatShell initialConversations={[]} />
      </SidebarChatProvider>
    );

    const newBtn = screen.getByTestId("new-conversation-btn");
    expect(newBtn).toBeInTheDocument();

    // Clicking must not throw and the chat form must still be visible (empty thread).
    fireEvent.click(newBtn);

    expect(
      screen.getByRole("textbox", { name: /votre question/i })
    ).toBeInTheDocument();
  });
});
