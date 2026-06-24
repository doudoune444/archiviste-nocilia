// AC CHAT (#249) — welcome state, suggestion chips, modern input, state transition.
//
// Seam 3 (unit, vitest + RTL): exercises behaviors the E2E covers poorly —
//   - keyboard handling (Enter submits, Shift+Enter inserts a newline)
//   - welcome → conversation form-state transition (centered ↔ bottom)
//   - suggestion chips send the exact question via the same path as form submit
//   - double-submit guard while streaming
//
// Tests assert observable behavior through the public component interface
// (rendered DOM + fetch calls), never internal state.

import { describe, it, expect, vi, afterEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
} from "@testing-library/react";
import { ChatForm } from "@/components/chat/ChatForm";

// chat.module.css — identity proxy so class names equal their keys in jsdom.
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

/** A stream that never resolves — keeps the component in the streaming state. */
function makePendingResponse(): Promise<Response> {
  return new Promise<Response>(() => {
    /* never resolves */
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ChatForm welcome state (#249)", () => {
  it("shows the welcome title and four suggestion chips on an empty thread", () => {
    render(<ChatForm />);

    expect(
      screen.getByRole("heading", { name: /Bienvenue aux archives de Nocilia/i })
    ).toBeInTheDocument();

    const chipLabels = [
      "Qui est Blowen ?",
      "Qu'est-ce que le Cérafon ?",
      "Qui a élu domicile dans les ruines de Periste ?",
      "Combien font 2+2 ?",
    ];
    for (const label of chipLabels) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("marks the form as centered when the thread is empty", () => {
    const { container } = render(<ChatForm />);
    expect(container.querySelector('[data-state="welcome"]')).toBeInTheDocument();
    expect(
      container.querySelector('[data-state="conversation"]')
    ).not.toBeInTheDocument();
  });

  it("does not show suggestion chips when a transcript is loaded", () => {
    render(
      <ChatForm
        initialMessages={[{ role: "user", text: "Bonjour" }]}
      />
    );
    expect(
      screen.queryByRole("button", { name: "Qui est Blowen ?" })
    ).not.toBeInTheDocument();
  });

  it("anchors to the conversation state when a transcript is loaded", () => {
    const { container } = render(
      <ChatForm initialMessages={[{ role: "user", text: "Bonjour" }]} />
    );
    expect(
      container.querySelector('[data-state="conversation"]')
    ).toBeInTheDocument();
    expect(
      container.querySelector('[data-state="welcome"]')
    ).not.toBeInTheDocument();
  });
});

describe("ChatForm suggestion chips (#249)", () => {
  it("sends the exact chip question via the chat stream path and switches to conversation state", async () => {
    const mockFetch = vi
      .fn()
      .mockImplementationOnce(() => Promise.resolve(makeSseResponse()));
    vi.stubGlobal("fetch", mockFetch);

    const { container } = render(<ChatForm />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Qui est Blowen ?" }));
    });

    // The exact chip text was sent to the stream endpoint.
    expect(mockFetch).toHaveBeenNthCalledWith(
      1,
      "/api/v1/chat/stream",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ query: "Qui est Blowen ?" }),
      })
    );

    // The question is echoed and the form is now in conversation state.
    await waitFor(() => {
      expect(screen.getByText("Qui est Blowen ?")).toBeInTheDocument();
    });
    expect(
      container.querySelector('[data-state="conversation"]')
    ).toBeInTheDocument();
  });
});

describe("ChatForm keyboard handling (#249)", () => {
  it("submits on Enter (no Shift) and sends the typed question", async () => {
    const mockFetch = vi
      .fn()
      .mockImplementationOnce(() => Promise.resolve(makeSseResponse()));
    vi.stubGlobal("fetch", mockFetch);

    render(<ChatForm />);
    const textarea = screen.getByRole("textbox", { name: /votre question/i });
    fireEvent.change(textarea, { target: { value: "Une question" } });

    await act(async () => {
      fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    });

    expect(mockFetch).toHaveBeenNthCalledWith(
      1,
      "/api/v1/chat/stream",
      expect.objectContaining({
        body: JSON.stringify({ query: "Une question" }),
      })
    );
  });

  it("does NOT submit on Shift+Enter (newline insertion is left to the textarea)", async () => {
    const mockFetch = vi.fn(() => Promise.resolve(makeSseResponse()));
    vi.stubGlobal("fetch", mockFetch);

    render(<ChatForm />);
    const textarea = screen.getByRole("textbox", { name: /votre question/i });
    fireEvent.change(textarea, { target: { value: "Ligne un" } });

    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });

    // No submit happened: the stream endpoint was never called.
    expect(mockFetch).not.toHaveBeenCalled();
  });
});

describe("ChatForm conversation_id persistence (#291)", () => {
  it("carries the meta conversation_id from message #1 into the body of message #2 (fresh conversation)", async () => {
    const mockFetch = vi
      .fn()
      .mockImplementationOnce(() => Promise.resolve(makeSseResponse()))
      .mockImplementationOnce(() => Promise.resolve(makeSseResponse()));
    vi.stubGlobal("fetch", mockFetch);

    render(<ChatForm />);
    const textarea = screen.getByRole("textbox", { name: /votre question/i });

    fireEvent.change(textarea, { target: { value: "Premier" } });
    await act(async () => {
      fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    });

    // Message #1 carries no conversation_id (none known yet).
    expect(mockFetch).toHaveBeenNthCalledWith(
      1,
      "/api/v1/chat/stream",
      expect.objectContaining({ body: JSON.stringify({ query: "Premier" }) })
    );

    await waitFor(() => expect(textarea).not.toBeDisabled());

    fireEvent.change(textarea, { target: { value: "Deuxième" } });
    await act(async () => {
      fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    });

    // Message #2 carries the conversation_id captured from message #1's meta.
    expect(mockFetch).toHaveBeenNthCalledWith(
      2,
      "/api/v1/chat/stream",
      expect.objectContaining({
        body: JSON.stringify({ query: "Deuxième", conversation_id: "c1" }),
      })
    );
  });

  it("includes initialConversationId in the very first message body (resumed conversation)", async () => {
    const mockFetch = vi
      .fn()
      .mockImplementationOnce(() => Promise.resolve(makeSseResponse()));
    vi.stubGlobal("fetch", mockFetch);

    render(
      <ChatForm
        initialConversationId="Y"
        initialMessages={[{ role: "user", text: "Bonjour" }]}
      />
    );
    const textarea = screen.getByRole("textbox", { name: /votre question/i });

    fireEvent.change(textarea, { target: { value: "Suite" } });
    await act(async () => {
      fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    });

    expect(mockFetch).toHaveBeenNthCalledWith(
      1,
      "/api/v1/chat/stream",
      expect.objectContaining({
        body: JSON.stringify({ query: "Suite", conversation_id: "Y" }),
      })
    );
  });
});

describe("ChatForm double-submit guard (#249)", () => {
  it("disables the textarea and ignores Enter while a response is streaming", async () => {
    const mockFetch = vi.fn().mockImplementationOnce(() => makePendingResponse());
    vi.stubGlobal("fetch", mockFetch);

    render(<ChatForm />);
    const textarea = screen.getByRole("textbox", { name: /votre question/i });

    fireEvent.change(textarea, { target: { value: "Première" } });
    await act(async () => {
      fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    });

    // The single in-flight stream call happened; the field is now disabled.
    expect(mockFetch).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(textarea).toBeDisabled();
    });

    // A second Enter while streaming must not trigger another stream call.
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });
});
