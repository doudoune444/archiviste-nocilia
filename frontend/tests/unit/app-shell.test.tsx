// AC #245 — AppShell: global sidebar present on every page; history + chat
// surface only on the Archiviste page (/); "Nouvelle conversation" navigates to
// / from other pages; mobile hamburger toggles the sidebar drawer.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
} from "@testing-library/react";

vi.mock("@/components/app-shell/AppShell.module.css", () => ({
  default: new Proxy({}, { get: (_t, prop: string) => prop }),
}));
vi.mock("@/components/chat-form/chat.module.css", () => ({
  default: new Proxy({}, { get: (_t, prop: string) => prop }),
}));
vi.mock("@/components/conversation-history/ConversationHistory.module.css", () => ({
  default: new Proxy({}, { get: (_t, prop: string) => prop }),
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

const mockPush = vi.fn();
const mockPathname = vi.fn<() => string>();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  usePathname: () => mockPathname(),
}));

import { AppShell } from "@/components/app-shell/AppShell";

beforeEach(() => {
  mockPush.mockReset();
  mockPathname.mockReset();
});

describe("AppShell on the chat page (/) — #245", () => {
  beforeEach(() => mockPathname.mockReturnValue("/"));

  it("renders the chat input directly (children ignored on the chat route)", () => {
    render(
      <AppShell tier="anonymous" email={null} initialConversations={[]}>
        <div>page enfant</div>
      </AppShell>
    );
    expect(
      screen.getByRole("textbox", { name: /votre question/i })
    ).toBeInTheDocument();
  });

  it("renders the conversation history on the chat route", () => {
    render(
      <AppShell
        tier="member"
        email="m@e.com"
        initialConversations={[
          {
            id: "c1",
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-02T00:00:00Z",
            message_count: 2,
            title: "Qui est Blowen ?",
          },
        ]}
      >
        <div>page enfant</div>
      </AppShell>
    );
    // The history item carries the title; scope to the item to avoid matching
    // the identically-worded suggestion chip in the welcome state.
    expect(screen.getByTestId("conversation-item-c1")).toHaveTextContent(
      "Qui est Blowen ?"
    );
  });
});

describe("AppShell on a non-chat page — #245", () => {
  beforeEach(() => mockPathname.mockReturnValue("/lacunes"));

  it("renders the page children, not the chat surface", () => {
    render(
      <AppShell tier="anonymous" email={null} initialConversations={[]}>
        <div>contenu lacunes</div>
      </AppShell>
    );
    expect(screen.getByText("contenu lacunes")).toBeInTheDocument();
    expect(
      screen.queryByRole("textbox", { name: /votre question/i })
    ).not.toBeInTheDocument();
  });

  it("does not render conversation history off the chat route", () => {
    render(
      <AppShell
        tier="member"
        email="m@e.com"
        initialConversations={[
          {
            id: "c1",
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-02T00:00:00Z",
            message_count: 2,
            title: "Qui est Blowen ?",
          },
        ]}
      >
        <div>contenu</div>
      </AppShell>
    );
    expect(screen.queryByText("Qui est Blowen ?")).not.toBeInTheDocument();
  });

  it("'Nouvelle conversation' navigates to / from a non-chat page", () => {
    render(
      <AppShell tier="anonymous" email={null} initialConversations={[]}>
        <div>contenu</div>
      </AppShell>
    );
    fireEvent.click(screen.getByTestId("new-conversation-btn"));
    expect(mockPush).toHaveBeenCalledWith("/");
  });
});

describe("AppShell mobile drawer — #245", () => {
  beforeEach(() => mockPathname.mockReturnValue("/"));

  it("exposes a hamburger button to open the sidebar", () => {
    render(
      <AppShell tier="anonymous" email={null} initialConversations={[]}>
        <div>contenu</div>
      </AppShell>
    );
    expect(
      screen.getByRole("button", { name: /ouvrir le menu/i })
    ).toBeInTheDocument();
  });
});

// B1 regression (carried over from the old ChatShell): the first exchange must
// NOT be wiped when the sidebar conversation list refreshes after sending.
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

function makeConversationsResponse(): Response {
  return new Response(
    JSON.stringify({
      conversations: [
        {
          id: "c1",
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:01:00Z",
          message_count: 2,
          title: "Qui est Nocilia ?",
        },
      ],
    }),
    { status: 200, headers: { "content-type": "application/json" } }
  );
}

describe("AppShell B1 regression — thread survives sidebar refresh (#245)", () => {
  beforeEach(() => mockPathname.mockReturnValue("/"));
  afterEach(() => vi.restoreAllMocks());

  it("keeps the sent message after the conversation list refreshes", async () => {
    const mockFetch = vi
      .fn()
      .mockImplementationOnce(() => Promise.resolve(makeSseResponse()))
      .mockImplementationOnce(() =>
        Promise.resolve(makeConversationsResponse())
      );
    vi.stubGlobal("fetch", mockFetch);

    render(
      <AppShell tier="anonymous" email={null} initialConversations={[]}>
        <div>contenu</div>
      </AppShell>
    );

    const textarea = screen.getByRole("textbox", { name: /votre question/i });
    fireEvent.change(textarea, { target: { value: "Qui est Nocilia ?" } });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /envoyer/i }));
    });

    // The optimistic user echo lives in the thread as a paragraph; after the
    // sidebar refresh the same text also appears as a history title, so scope to
    // the thread paragraph to prove the thread was not wiped.
    await waitFor(() => {
      const echoes = screen
        .getAllByText("Qui est Nocilia ?")
        .filter((node) => node.tagName === "P");
      expect(echoes.length).toBeGreaterThan(0);
    });
    await waitFor(() => {
      const answers = screen.getAllByTestId("assistant-answer");
      expect(answers[0]).toHaveTextContent("Réponse.");
    });
    expect(mockFetch).toHaveBeenNthCalledWith(2, "/api/v1/conversations");
  });
});
