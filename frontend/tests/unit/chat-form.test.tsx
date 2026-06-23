// AC #245 — ChatForm welcome state, suggestion chips, keyboard handling.
//
// Welcome state (empty thread, no transcript loaded): short welcome heading,
// centered input, four suggestion chips. Clicking a chip sends that question
// immediately (same path as form submit) and switches to conversation state.
// Enter submits; Shift+Enter inserts a newline. Send button is an icon button.

import { describe, it, expect, vi, afterEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
} from "@testing-library/react";
import { ChatForm } from "@/components/chat-form/ChatForm";

vi.mock("@/components/chat-form/chat.module.css", () => ({
  default: new Proxy({}, { get: (_t, prop: string) => prop }),
}));

/** SSE stream emitting one token then done. */
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

describe("ChatForm welcome state (#245)", () => {
  it("shows the welcome heading when the thread is empty", () => {
    render(<ChatForm initialMessages={[]} />);
    expect(
      screen.getByRole("heading", { level: 1 })
    ).toHaveTextContent("Bienvenue aux archives de Nocilia");
  });

  it("renders four suggestion chips in the welcome state", () => {
    render(<ChatForm initialMessages={[]} />);
    expect(
      screen.getByRole("button", { name: "Qui est Blowen ?" })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Qu'est-ce que le Cérafon ?" })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "Qui a élu domicile dans les ruines de Periste ?",
      })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Combien font 2+2 ?" })
    ).toBeInTheDocument();
  });

  it("hides the welcome heading once a transcript is loaded", () => {
    render(
      <ChatForm
        initialMessages={[{ role: "user", text: "déjà posée" }]}
      />
    );
    expect(
      screen.queryByRole("heading", { name: /bienvenue aux archives/i })
    ).not.toBeInTheDocument();
  });

  it("does not show chips once a transcript is loaded", () => {
    render(
      <ChatForm initialMessages={[{ role: "user", text: "déjà posée" }]} />
    );
    expect(
      screen.queryByRole("button", { name: "Qui est Blowen ?" })
    ).not.toBeInTheDocument();
  });
});

describe("ChatForm suggestion chips send immediately (#245)", () => {
  it("clicking a chip sends that question and switches out of the welcome state", async () => {
    const mockFetch = vi
      .fn()
      .mockImplementationOnce(() => Promise.resolve(makeSseResponse()));
    vi.stubGlobal("fetch", mockFetch);

    render(<ChatForm initialMessages={[]} />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Qui est Blowen ?" }));
    });

    // The clicked question is echoed in the thread.
    await waitFor(() => {
      expect(screen.getByText("Qui est Blowen ?")).toBeInTheDocument();
    });
    // The stream endpoint was called with the chip text.
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/v1/chat/stream",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ query: "Qui est Blowen ?" }),
      })
    );
    // Welcome heading is gone after sending.
    expect(
      screen.queryByRole("heading", { name: /bienvenue aux archives/i })
    ).not.toBeInTheDocument();
  });
});

describe("ChatForm keyboard handling (#245)", () => {
  it("Enter submits the question", async () => {
    const mockFetch = vi
      .fn()
      .mockImplementationOnce(() => Promise.resolve(makeSseResponse()));
    vi.stubGlobal("fetch", mockFetch);

    render(<ChatForm initialMessages={[]} />);
    const textarea = screen.getByRole("textbox", { name: /votre question/i });
    fireEvent.change(textarea, { target: { value: "Qui est Nocilia ?" } });

    await act(async () => {
      fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    });

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/v1/chat/stream",
      expect.objectContaining({
        body: JSON.stringify({ query: "Qui est Nocilia ?" }),
      })
    );
  });

  it("Shift+Enter does NOT submit (newline insertion)", () => {
    const mockFetch = vi.fn();
    vi.stubGlobal("fetch", mockFetch);

    render(<ChatForm initialMessages={[]} />);
    const textarea = screen.getByRole("textbox", { name: /votre question/i });
    fireEvent.change(textarea, { target: { value: "ligne un" } });

    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });

    expect(mockFetch).not.toHaveBeenCalled();
  });
});
