// B2 regression — switching conversations must never show another
// conversation's transcript.
//
// Root cause (old code): handleSelectConversation set selectedId BEFORE awaiting
// the transcript fetch. ChatForm is keyed by selectedId, so it remounted at once
// with the STALE loadedMessages (the previously open conversation), and the later
// setLoadedMessages could not fix it (key unchanged → no remount). Fast switches
// had no stale-response guard either, so an out-of-order fetch could win.
//
// Fix: commit transcript + selectedId together (one render) AFTER the fetch, and
// drop any response whose selection token was superseded.

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import React from "react";
import { ChatShell } from "@/components/conversation-history/ChatShell";
import { SidebarChatProvider } from "@/components/app-sidebar/SidebarChatContext";
import { SidebarShell } from "@/components/app-sidebar/SidebarShell";
import type { ConversationSummary } from "@/components/conversation-history/types";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/",
}));
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));
vi.mock("@/components/chat/chat.module.css", () => ({
  default: new Proxy({}, { get: (_t, p: string) => p }),
}));
vi.mock(
  "@/components/conversation-history/ConversationHistory.module.css",
  () => ({ default: new Proxy({}, { get: (_t, p: string) => p }) })
);
vi.mock("@/components/conversation-history/ChatShell.module.css", () => ({
  default: new Proxy({}, { get: (_t, p: string) => p }),
}));

function conv(id: string, title: string): ConversationSummary {
  return {
    id,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T10:30:00Z",
    message_count: 2,
    title,
    has_ticket: false,
  };
}

/** A transcript response with one distinct user turn per conversation. */
function messagesResponse(marker: string): Response {
  return new Response(
    JSON.stringify({
      conversation_id: marker,
      messages: [{ role: "user", ordinal: 0, content: `Question ${marker}` }],
    }),
    { status: 200, headers: { "content-type": "application/json" } }
  );
}

interface Deferred {
  promise: Promise<Response>;
  resolve: (response: Response) => void;
}
function deferred(): Deferred {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function renderShell() {
  return render(
    <SidebarChatProvider>
      <SidebarShell identity={{ tier: "anonymous", email: null }} />
      <ChatShell
        initialConversations={[conv("c1", "Capitale"), conv("c2", "Lore")]}
      />
    </SidebarChatProvider>
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ChatShell select — B2 transcript never crosses conversations", () => {
  it("shows each conversation's own transcript on sequential switches", async () => {
    const mockFetch = vi.fn((url: string) => {
      if (url.includes("/c1/")) return Promise.resolve(messagesResponse("c1"));
      if (url.includes("/c2/")) return Promise.resolve(messagesResponse("c2"));
      throw new Error(`unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", mockFetch);

    renderShell();

    await act(async () => {
      fireEvent.click(screen.getByTestId("conversation-item-c1"));
    });
    await waitFor(() =>
      expect(screen.getByText("Question c1")).toBeInTheDocument()
    );

    await act(async () => {
      fireEvent.click(screen.getByTestId("conversation-item-c2"));
    });
    // The crux: after switching to c2, c1's turn must be gone.
    await waitFor(() =>
      expect(screen.getByText("Question c2")).toBeInTheDocument()
    );
    expect(screen.queryByText("Question c1")).not.toBeInTheDocument();
  });

  it("ignores a stale out-of-order response from a superseded selection", async () => {
    const first = deferred();
    const second = deferred();
    const mockFetch = vi.fn((url: string) => {
      if (url.includes("/c1/")) return first.promise;
      if (url.includes("/c2/")) return second.promise;
      throw new Error(`unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", mockFetch);

    renderShell();

    // Click c1 then c2 — both fetches in flight.
    fireEvent.click(screen.getByTestId("conversation-item-c1"));
    fireEvent.click(screen.getByTestId("conversation-item-c2"));

    // Resolve the LATEST selection (c2) first, then the stale one (c1).
    await act(async () => {
      second.resolve(messagesResponse("c2"));
    });
    await act(async () => {
      first.resolve(messagesResponse("c1"));
    });

    await waitFor(() =>
      expect(screen.getByText("Question c2")).toBeInTheDocument()
    );
    // The superseded c1 response must never overwrite the c2 transcript.
    expect(screen.queryByText("Question c1")).not.toBeInTheDocument();
  });
});
