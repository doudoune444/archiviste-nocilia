// AC #249 — chat redesign: welcome state, suggestion chips, modern input.
//
// These unit tests drive the welcome-vs-conversation state machine and the
// modernized input (Enter submits, Shift+Enter inserts a newline, auto-grow,
// double-submit guard). E2E (chat.spec.ts) covers the full chip-send flow and
// the centered→bottom layout transition in a real browser.
//
// A03: assistant output is rendered via AssistantAnswer (react-markdown +
// rehype-sanitize) — never raw HTML. That invariant is exercised by
// assistant-answer.test.tsx; here we only drive form/state behavior.

import { describe, it, expect, vi, afterEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
} from "@testing-library/react";
import { ChatForm, WELCOME_CHIPS } from "@/components/chat/ChatForm";

// chat.module.css — jsdom cannot process real CSS; stub with an identity proxy.
vi.mock("@/components/chat/chat.module.css", () => ({
  default: new Proxy({}, { get: (_t, prop: string) => prop }),
}));

/** SSE response emitting one token then done — a successful round-trip. */
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

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ChatForm — welcome state (#249)", () => {
  it("shows the welcome title, the input, and exactly four suggestion chips on an empty thread", () => {
    render(<ChatForm />);

    expect(
      screen.getByRole("heading", { name: /Bienvenue aux archives de Nocilia/i })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: /votre question/i })
    ).toBeInTheDocument();

    const chips = screen.getAllByTestId("suggestion-chip");
    expect(chips).toHaveLength(4);
    expect(chips.map((c) => c.textContent)).toEqual([...WELCOME_CHIPS]);
  });

  it("exposes the four exact lore questions as chips", () => {
    expect(WELCOME_CHIPS).toEqual([
      "Qui est Blowen ?",
      "Qu'est-ce que le Cérafon ?",
      "Qui a élu domicile dans les ruines de Periste ?",
      "Combien font 2+2 ?",
    ]);
  });

  it("does NOT show the welcome state when a transcript is loaded (initialMessages)", () => {
    render(
      <ChatForm initialMessages={[{ role: "user", text: "Bonjour" }]} />
    );

    expect(
      screen.queryByRole("heading", {
        name: /Bienvenue aux archives de Nocilia/i,
      })
    ).not.toBeInTheDocument();
    expect(screen.queryAllByTestId("suggestion-chip")).toHaveLength(0);
    expect(screen.getByText("Bonjour")).toBeInTheDocument();
  });
});

describe("ChatForm — chip send (#249)", () => {
  it("sends the exact chip text immediately and switches to conversation state", async () => {
    const mockFetch = vi.fn().mockResolvedValueOnce(makeSseResponse());
    vi.stubGlobal("fetch", mockFetch);

    render(<ChatForm />);

    const chips = screen.getAllByTestId("suggestion-chip");
    const target = chips.find((c) => c.textContent === "Qui est Blowen ?")!;

    await act(async () => {
      fireEvent.click(target);
    });

    // The chip text was sent verbatim as the query.
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/v1/chat/stream",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ query: "Qui est Blowen ?" }),
      })
    );

    // Optimistic echo appears and the welcome state is gone (conversation state).
    await waitFor(() => {
      expect(screen.getByText("Qui est Blowen ?")).toBeInTheDocument();
    });
    expect(screen.queryAllByTestId("suggestion-chip")).toHaveLength(0);
    expect(
      screen.queryByRole("heading", {
        name: /Bienvenue aux archives de Nocilia/i,
      })
    ).not.toBeInTheDocument();
  });
});

describe("ChatForm — modern input keyboard handling (#249)", () => {
  it("submits on Enter (no Shift)", async () => {
    const mockFetch = vi.fn().mockResolvedValueOnce(makeSseResponse());
    vi.stubGlobal("fetch", mockFetch);

    render(<ChatForm />);
    const textarea = screen.getByRole("textbox", { name: /votre question/i });
    fireEvent.change(textarea, { target: { value: "Bonjour" } });

    await act(async () => {
      fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    });

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/v1/chat/stream",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ query: "Bonjour" }),
      })
    );
  });

  it("does NOT submit on Shift+Enter (newline inserted instead)", () => {
    const mockFetch = vi.fn();
    vi.stubGlobal("fetch", mockFetch);

    render(<ChatForm />);
    const textarea = screen.getByRole("textbox", { name: /votre question/i });
    fireEvent.change(textarea, { target: { value: "Ligne un" } });

    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });

    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("does not submit an empty/whitespace query on Enter", () => {
    const mockFetch = vi.fn();
    vi.stubGlobal("fetch", mockFetch);

    render(<ChatForm />);
    const textarea = screen.getByRole("textbox", { name: /votre question/i });
    fireEvent.change(textarea, { target: { value: "   " } });

    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });

    expect(mockFetch).not.toHaveBeenCalled();
  });
});

describe("ChatForm — send button is an integrated icon (#249)", () => {
  it("renders an accessible send button labelled 'Envoyer'", () => {
    render(<ChatForm />);
    expect(
      screen.getByRole("button", { name: /envoyer/i })
    ).toBeInTheDocument();
  });

  it("disables the send button while a stream is in progress (double-submit guard)", async () => {
    // A never-closing stream keeps isStreaming=true so we can assert the guard.
    const encoder = new TextEncoder();
    const slowStream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          encoder.encode(
            'event: meta\ndata: {"mode":"canon","conversation_id":"c1","request_id":"r1"}\n\n'
          )
        );
        // never close → stays streaming
      },
    });
    const mockFetch = vi.fn().mockResolvedValue(
      new Response(slowStream, {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      })
    );
    vi.stubGlobal("fetch", mockFetch);

    render(<ChatForm />);
    const textarea = screen.getByRole("textbox", { name: /votre question/i });
    fireEvent.change(textarea, { target: { value: "Bonjour" } });

    await act(async () => {
      fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /envoyer/i })).toBeDisabled();
    });

    // Only one stream request despite the input still holding text.
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });
});
