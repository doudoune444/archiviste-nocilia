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
import { mapTranscriptToMessages } from "@/components/conversation-history/transcript";
import type { ConversationMessage } from "@/components/conversation-history/types";

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

/**
 * SSE response whose token stream carries the raw `---SUIVI---` sentinel block
 * (workers stream tokens verbatim, #354) and whose done event carries the
 * structured followups. Mirrors the real worker wire output.
 */
function makeSseResponseWithFollowups(): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(
        encoder.encode(
          'event: meta\ndata: {"mode":"canon","conversation_id":"c1","request_id":"r1"}\n\n'
        )
      );
      controller.enqueue(
        encoder.encode(
          'event: token\ndata: {"text":"Corps de réponse.\\n---SUIVI---\\n- Suite A ?\\n- Suite B ?"}\n\n'
        )
      );
      controller.enqueue(
        encoder.encode(
          'event: done\ndata: {"citations":[],"usage":{},"retrieve_ms":0,"llm_ms":0,"followups":["Suite A ?","Suite B ?"]}\n\n'
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

describe("ChatForm assistant turn header (#326)", () => {
  it("renders a turn header with the 🪶 avatar and the « Archiviste » label for an assistant turn", () => {
    render(
      <ChatForm
        initialMessages={[
          { role: "assistant", text: "Une réponse.", mode: "canon" },
        ]}
      />
    );

    const header = screen.getByTestId("turn-header");
    expect(header).toBeInTheDocument();
    expect(header.textContent).toContain("🪶");
    expect(header.textContent).toContain("Archiviste");
  });

  it("moves the mode-chip into the turn header (above the assistant-answer body)", () => {
    render(
      <ChatForm
        initialMessages={[
          { role: "assistant", text: "Une réponse.", mode: "canon" },
        ]}
      />
    );

    const chip = screen.getByTestId("mode-chip");
    expect(chip).toHaveTextContent("canon");

    // The chip now lives in the turn header, not inside the answer body.
    const header = screen.getByTestId("turn-header");
    expect(header).toContainElement(chip);
    const answer = screen.getByTestId("assistant-answer");
    expect(answer).not.toContainElement(chip);
  });

  it("does not render a mode-chip when the assistant turn has no mode", () => {
    render(
      <ChatForm
        initialMessages={[{ role: "assistant", text: "Sans mode." }]}
      />
    );

    expect(screen.queryByTestId("mode-chip")).not.toBeInTheDocument();
    // Header (avatar + label) is shown regardless of mode.
    expect(screen.getByTestId("turn-header")).toBeInTheDocument();
  });

  it("separates the turn header from the body with a horizontal rule", () => {
    const { container } = render(
      <ChatForm
        initialMessages={[
          { role: "assistant", text: "Une réponse.", mode: "canon" },
        ]}
      />
    );

    expect(container.querySelector("hr")).toBeInTheDocument();
  });

  it("still renders the assistant-answer body for an assistant turn", () => {
    render(
      <ChatForm
        initialMessages={[
          { role: "assistant", text: "Une réponse.", mode: "canon" },
        ]}
      />
    );

    expect(screen.getByTestId("assistant-answer")).toBeInTheDocument();
  });

  it("renders a turn header with the « Vous » label for a user turn", () => {
    render(
      <ChatForm initialMessages={[{ role: "user", text: "Ma question" }]} />
    );

    const header = screen.getByTestId("turn-header");
    expect(header).toBeInTheDocument();
    expect(header.textContent).toContain("Vous");
  });
});

describe("ChatForm follow-up pills (#355)", () => {
  it("renders followups as buttons under the assistant turn", () => {
    render(
      <ChatForm
        initialMessages={[
          {
            role: "assistant",
            text: "Une réponse.",
            followups: ["Comment ça marche ?", "Et après ?"],
          },
        ]}
      />
    );

    expect(
      screen.getByRole("button", { name: "Comment ça marche ?" })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Et après ?" })
    ).toBeInTheDocument();
  });

  it("does not render any follow-up pill when followups is empty or absent", () => {
    render(
      <ChatForm
        initialMessages={[
          { role: "assistant", text: "Sans suivi.", followups: [] },
          { role: "assistant", text: "Pas de champ." },
        ]}
      />
    );

    expect(
      screen.queryByRole("button", { name: /\?$/ })
    ).not.toBeInTheDocument();
  });

  it("relaunches a query through the chat stream path when a follow-up is clicked", async () => {
    const mockFetch = vi
      .fn()
      .mockImplementationOnce(() => Promise.resolve(makeSseResponse()));
    vi.stubGlobal("fetch", mockFetch);

    render(
      <ChatForm
        initialMessages={[
          {
            role: "assistant",
            text: "Une réponse.",
            followups: ["Et après ?"],
          },
        ]}
      />
    );

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Et après ?" }));
    });

    expect(mockFetch).toHaveBeenNthCalledWith(
      1,
      "/api/v1/chat/stream",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ query: "Et après ?" }),
      })
    );
  });
});

describe("ChatForm follow-up streaming consumption (#355)", () => {
  it("attaches done.followups to the committed assistant turn as pills", async () => {
    const mockFetch = vi
      .fn()
      .mockImplementationOnce(() =>
        Promise.resolve(makeSseResponseWithFollowups())
      );
    vi.stubGlobal("fetch", mockFetch);

    render(<ChatForm />);
    const textarea = screen.getByRole("textbox", { name: /votre question/i });
    fireEvent.change(textarea, { target: { value: "Ma question" } });

    await act(async () => {
      fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    });

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Suite A ?" })
      ).toBeInTheDocument();
    });
    expect(
      screen.getByRole("button", { name: "Suite B ?" })
    ).toBeInTheDocument();
  });

  it("never shows the ---SUIVI--- marker or its block in the committed answer", async () => {
    const mockFetch = vi
      .fn()
      .mockImplementationOnce(() =>
        Promise.resolve(makeSseResponseWithFollowups())
      );
    vi.stubGlobal("fetch", mockFetch);

    render(<ChatForm />);
    const textarea = screen.getByRole("textbox", { name: /votre question/i });
    fireEvent.change(textarea, { target: { value: "Ma question" } });

    await act(async () => {
      fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    });

    await waitFor(() => {
      expect(screen.getByTestId("assistant-answer")).toBeInTheDocument();
    });
    const answer = screen.getByTestId("assistant-answer");
    expect(answer.textContent).toContain("Corps de réponse.");
    expect(answer.textContent).not.toContain("---SUIVI---");
    expect(answer.textContent).not.toContain("Suite A ?");
  });
});

describe("ChatForm follow-up stream masking (#355)", () => {
  it("masks the ---SUIVI--- block in the live streaming view before done arrives", async () => {
    const encoder = new TextEncoder();
    // Open stream: emits meta + a token carrying the raw sentinel block, then
    // stays open (no done) so the assertion observes the mid-stream view.
    const response = new Response(
      new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(
            encoder.encode(
              'event: meta\ndata: {"mode":"canon","conversation_id":"c1","request_id":"r1"}\n\n'
            )
          );
          controller.enqueue(
            encoder.encode(
              'event: token\ndata: {"text":"Texte visible.\\n---SUIVI---\\n- Caché ?"}\n\n'
            )
          );
        },
      }),
      { status: 200, headers: { "content-type": "text/event-stream" } }
    );
    const mockFetch = vi
      .fn()
      .mockImplementationOnce(() => Promise.resolve(response));
    vi.stubGlobal("fetch", mockFetch);

    render(<ChatForm />);
    const textarea = screen.getByRole("textbox", { name: /votre question/i });
    fireEvent.change(textarea, { target: { value: "Ma question" } });

    await act(async () => {
      fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    });

    await waitFor(() => {
      const streaming = screen.getByTestId("streaming-answer");
      expect(streaming.textContent).toContain("Texte visible.");
    });
    const streaming = screen.getByTestId("streaming-answer");
    expect(streaming.textContent).not.toContain("---SUIVI---");
    expect(streaming.textContent).not.toContain("Caché ?");
  });
});

describe("ChatForm committed answer — citations + follow-ups together (#345)", () => {
  it("renders both multi-source superscript citations and follow-up pills", () => {
    const { container } = render(
      <ChatForm
        initialMessages={[
          {
            role: "assistant",
            text: "Une affirmation étayée [lore/a.md, lore/b.md].",
            citations: [
              { source_path: "lore/a.md", chunk_ords: [0] },
              { source_path: "lore/b.md", chunk_ords: [1] },
            ],
            followups: ["Et ensuite ?", "Pourquoi ?"],
          },
        ]}
      />
    );

    // BUG B: the comma-grouped bracket yields two superscripts ¹².
    const sups = container.querySelectorAll("sup.fn");
    expect(Array.from(sups, (s) => s.textContent)).toEqual(["1", "2"]);

    // #355 follow-up pills render alongside.
    expect(
      screen.getByRole("button", { name: "Et ensuite ?" })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Pourquoi ?" })
    ).toBeInTheDocument();
  });
});

describe("ChatForm follow-up tolerant marker masking (#345)", () => {
  it("masks a tolerant '--- SUIVI ---' variant in the committed answer", async () => {
    const encoder = new TextEncoder();
    const response = new Response(
      new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(
            encoder.encode(
              'event: meta\ndata: {"mode":"canon","conversation_id":"c1","request_id":"r1"}\n\n'
            )
          );
          controller.enqueue(
            encoder.encode(
              'event: token\ndata: {"text":"Corps tolérant.\\n--- SUIVI ---\\n- Caché ?"}\n\n'
            )
          );
          controller.enqueue(
            encoder.encode(
              'event: done\ndata: {"citations":[],"usage":{},"retrieve_ms":0,"llm_ms":0,"followups":["Caché ?"]}\n\n'
            )
          );
          controller.close();
        },
      }),
      { status: 200, headers: { "content-type": "text/event-stream" } }
    );
    const mockFetch = vi
      .fn()
      .mockImplementationOnce(() => Promise.resolve(response));
    vi.stubGlobal("fetch", mockFetch);

    render(<ChatForm />);
    const textarea = screen.getByRole("textbox", { name: /votre question/i });
    fireEvent.change(textarea, { target: { value: "Ma question" } });

    await act(async () => {
      fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    });

    await waitFor(() => {
      expect(screen.getByTestId("assistant-answer")).toBeInTheDocument();
    });
    const answer = screen.getByTestId("assistant-answer");
    expect(answer.textContent).toContain("Corps tolérant.");
    expect(answer.textContent).not.toContain("SUIVI");
  });
});

describe("ChatForm re-hydration from history (#375)", () => {
  // AC #375: a conversation reloaded from history renders the SAME rich turn as
  // when it was streamed — pills, superscript citations, sources panel and the
  // per-answer signal form — with no raw sentinel or bracket markers leaking.
  const CONVERSATION_ID = "conv-reload";
  const rows: ConversationMessage[] = [
    { role: "user", ordinal: 0, content: "Qui est Blowen ?" },
    {
      role: "assistant",
      ordinal: 1,
      content:
        "Blowen vécut à Periste [lore/blowen.md, lore/periste.md].\n---SUIVI---\n- Où est Periste ?\n- Qui d'autre y vécut ?",
    },
  ];

  function renderReloaded() {
    return render(
      <ChatForm
        initialMessages={mapTranscriptToMessages(rows, CONVERSATION_ID)}
        initialConversationId={CONVERSATION_ID}
      />
    );
  }

  it("renders the persisted follow-up block as clickable pills", () => {
    renderReloaded();
    expect(
      screen.getByRole("button", { name: "Où est Periste ?" })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Qui d'autre y vécut ?" })
    ).toBeInTheDocument();
  });

  it("re-hydrates inline markers into numbered superscript citations", () => {
    const { container } = renderReloaded();
    const sups = container.querySelectorAll("sup.fn");
    expect(Array.from(sups, (s) => s.textContent)).toEqual(["1", "2"]);
  });

  it("re-hydrates the sources panel from the inline markers", () => {
    renderReloaded();
    const summary = document.querySelector("details.sources summary");
    expect(summary?.textContent).toContain("Sources (2)");
  });

  it("re-attaches the per-answer signal form via the conversation id", () => {
    renderReloaded();
    expect(
      screen.getByRole("button", { name: /signaler une incohérence/i })
    ).toBeInTheDocument();
  });

  it("never leaks the raw ---SUIVI--- block or bracket markers into the body", () => {
    renderReloaded();
    const answer = screen.getByTestId("assistant-answer");
    expect(answer.textContent).toContain("Blowen vécut à Periste");
    expect(answer.textContent).not.toContain("SUIVI");
    expect(answer.textContent).not.toContain("[lore/blowen.md");
    expect(answer.textContent).not.toContain("Où est Periste ?");
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
