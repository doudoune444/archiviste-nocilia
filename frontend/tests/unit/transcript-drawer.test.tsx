// AC DASH-002: transcript drawer unit tests
//
// AC: author-only per-row affordance (NOT on public board)
// AC: opening loads turns, renders in order
// AC: turns rendered through AssistantAnswer (sanitized markdown, no raw HTML)
// AC: load failure shows error with request id
// AC: closing the drawer returns to list state

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { TicketTable } from "@/components/board/TicketTable";
import { TranscriptDrawer, fetchTranscript } from "@/components/dashboard/TranscriptDrawer";
import type { BoardTicket } from "@/components/board/types";
import type { Message } from "@/components/conversation-history/types";

// ---------------------------------------------------------------------------
// CSS module stubs (jsdom cannot process real CSS modules)
// ---------------------------------------------------------------------------

vi.mock("@/components/dashboard/TranscriptDrawer.module.css", () => ({
  default: new Proxy({}, { get: (_t, prop: string) => prop }),
}));

vi.mock("@/components/assistant-answer/AssistantAnswer.module.css", () => ({
  default: new Proxy({}, { get: (_t, prop: string) => prop }),
}));

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeTicket(overrides: Partial<BoardTicket> = {}): BoardTicket {
  return {
    id: "00000000-0000-4000-8000-000000000001",
    conversation_id: "00000000-0000-4000-8000-000000000099",
    question: "Qui est la gardienne du temple ?",
    category: "personnages",
    priority_score: 3,
    status: "open",
    created_at: "2026-01-15T10:00:00Z",
    updated_at: "2026-01-15T10:00:00Z",
    judges_not_passed: false,
    ...overrides,
  };
}

const LOADED_MESSAGES: Message[] = [
  { role: "user", text: "Qui est Nocilia ?" },
  { role: "assistant", text: "Nocilia est la gardienne du temple." },
];

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// AC: affordance hidden when onOpenTranscript not provided (public board)
// ---------------------------------------------------------------------------

describe("TicketTable — transcript affordance (DASH-002 AC)", () => {
  it("does NOT render the transcript button when onOpenTranscript is not provided", () => {
    render(<TicketTable tickets={[makeTicket()]} />);
    expect(screen.queryByTestId("open-transcript-btn")).toBeNull();
  });

  it("renders the transcript button when onOpenTranscript is provided", () => {
    render(
      <TicketTable
        tickets={[makeTicket()]}
        onOpenTranscript={() => undefined}
      />
    );
    expect(screen.getByTestId("open-transcript-btn")).toBeInTheDocument();
  });

  it("calls onOpenTranscript with the ticket when the button is clicked", () => {
    const onOpen = vi.fn();
    const ticket = makeTicket();
    render(<TicketTable tickets={[ticket]} onOpenTranscript={onOpen} />);
    fireEvent.click(screen.getByTestId("open-transcript-btn"));
    expect(onOpen).toHaveBeenCalledOnce();
    expect(onOpen).toHaveBeenCalledWith(ticket);
  });

  it("does not add the Transcript column header when no onOpenTranscript prop", () => {
    render(<TicketTable tickets={[]} />);
    expect(screen.queryByText("Transcript")).toBeNull();
  });

  it("adds the Transcript column header when onOpenTranscript is provided", () => {
    render(
      <TicketTable tickets={[]} onOpenTranscript={() => undefined} />
    );
    expect(screen.getByText("Transcript")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AC: drawer renders turns in order via AssistantAnswer
// ---------------------------------------------------------------------------

describe("TranscriptDrawer — turn rendering (DASH-002 AC)", () => {
  it("renders mapped turns in insertion order when loaded", () => {
    const onClose = vi.fn();
    render(
      <TranscriptDrawer
        ticket={makeTicket()}
        drawerState={{ status: "loaded", messages: LOADED_MESSAGES }}
        onClose={onClose}
      />
    );

    // Both turns are rendered through AssistantAnswer (data-testid="assistant-answer")
    const answers = screen.getAllByTestId("assistant-answer");
    expect(answers).toHaveLength(2);
    expect(answers[0]).toHaveTextContent("Qui est Nocilia ?");
    expect(answers[1]).toHaveTextContent("Nocilia est la gardienne du temple.");
  });

  it("shows loading state while fetching", () => {
    render(
      <TranscriptDrawer
        ticket={makeTicket()}
        drawerState={{ status: "loading" }}
        onClose={() => undefined}
      />
    );
    expect(screen.getByRole("status")).toHaveTextContent("Chargement");
  });

  it("shows error with request id on failed fetch", () => {
    render(
      <TranscriptDrawer
        ticket={makeTicket()}
        drawerState={{ status: "error", requestId: "req-abc-123" }}
        onClose={() => undefined}
      />
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Impossible de charger le transcript");
    expect(alert).toHaveTextContent("req-abc-123");
  });

  it("calls onClose when the close button is clicked", () => {
    const onClose = vi.fn();
    render(
      <TranscriptDrawer
        ticket={makeTicket()}
        drawerState={{ status: "loaded", messages: LOADED_MESSAGES }}
        onClose={onClose}
      />
    );
    fireEvent.click(screen.getByTestId("close-drawer-btn"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("calls onClose when ESC is pressed", () => {
    const onClose = vi.fn();
    render(
      <TranscriptDrawer
        ticket={makeTicket()}
        drawerState={{ status: "loaded", messages: LOADED_MESSAGES }}
        onClose={onClose}
      />
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });
});

// ---------------------------------------------------------------------------
// AC: fetchTranscript — error state with request id
// ---------------------------------------------------------------------------

describe("fetchTranscript (DASH-002 AC)", () => {
  it("returns ok=false with request id on non-ok response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(null, {
          status: 500,
          headers: { "x-request-id": "req-fail-999" },
        })
      )
    );

    const result = await fetchTranscript("conv-id-1");
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.requestId).toBe("req-fail-999");
    }
  });

  it("returns ok=false with 'inconnu' when x-request-id header absent", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(null, { status: 503 }))
    );

    const result = await fetchTranscript("conv-id-2");
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.requestId).toBe("inconnu");
    }
  });

  it("returns ok=false with 'inconnu' when fetch throws (network error)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("Network error"))
    );

    const result = await fetchTranscript("conv-id-3");
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.requestId).toBe("inconnu");
    }
  });

  it("returns ok=true with sorted messages on valid response", async () => {
    const body = {
      conversation_id: "conv-id-ok",
      messages: [
        { role: "assistant", ordinal: 2, content: "Réponse." },
        { role: "user", ordinal: 1, content: "Question ?" },
      ],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { "content-type": "application/json" },
        })
      )
    );

    const result = await fetchTranscript("conv-id-ok");
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.messages).toHaveLength(2);
      // AC: turns rendered in ordinal order (user first, then assistant)
      expect(result.messages[0]?.role).toBe("user");
      expect(result.messages[0]?.text).toBe("Question ?");
      expect(result.messages[1]?.role).toBe("assistant");
      expect(result.messages[1]?.text).toBe("Réponse.");
    }
  });

  it("returns ok=false when response body shape is unexpected", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ unexpected: true }), {
          status: 200,
          headers: {
            "content-type": "application/json",
            "x-request-id": "req-shape-drift",
          },
        })
      )
    );

    const result = await fetchTranscript("conv-id-bad-shape");
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.requestId).toBe("req-shape-drift");
    }
  });
});

// ---------------------------------------------------------------------------
// AC: security — no raw HTML in turn content (AssistantAnswer is the only renderer)
// ---------------------------------------------------------------------------

describe("TranscriptDrawer — output sanitization (security.md)", () => {
  it("renders turn text through AssistantAnswer — no raw script injection", () => {
    const xssAttempt = '<script>alert("xss")</script>';
    render(
      <TranscriptDrawer
        ticket={makeTicket()}
        drawerState={{
          status: "loaded",
          messages: [{ role: "user", text: xssAttempt }],
        }}
        onClose={() => undefined}
      />
    );
    // AssistantAnswer must render via react-markdown — no <script> tag in DOM
    expect(document.querySelector("script")).toBeNull();
    // The text content is sanitized/stripped — no raw injection
    const answer = screen.getByTestId("assistant-answer");
    expect(answer).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Integration: waitFor async flow with mocked fetch (drawer open → loaded)
// ---------------------------------------------------------------------------

describe("DashboardTickets integration — open drawer then close", () => {
  it("clicking backdrop calls onClose", () => {
    const onClose = vi.fn();
    render(
      <TranscriptDrawer
        ticket={makeTicket()}
        drawerState={{ status: "loaded", messages: LOADED_MESSAGES }}
        onClose={onClose}
      />
    );
    fireEvent.click(screen.getByTestId("drawer-backdrop"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("drawer is not visible when no ticket is open (DashboardTickets wrapper renders without drawer)", async () => {
    // Stub fetch for board pagination (not needed here but guards against accidental calls)
    vi.stubGlobal("fetch", vi.fn());

    // DashboardTickets is a client component — import and render it
    const { DashboardTickets } = await import(
      "@/components/dashboard/DashboardTickets"
    );

    render(
      <DashboardTickets
        initialTickets={[makeTicket()]}
        total={1}
        filter={{ category: null, sort: "priority" }}
        apiPath="/api/v1/tickets"
      />
    );

    // Drawer not present on initial render
    expect(screen.queryByTestId("transcript-drawer")).toBeNull();
  });

  it("clicking open-transcript button shows the drawer with loading state then loaded", async () => {
    const body = {
      conversation_id: "conv-99",
      messages: [{ role: "user", ordinal: 1, content: "Hello ?" }],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { "content-type": "application/json" },
        })
      )
    );

    const { DashboardTickets } = await import(
      "@/components/dashboard/DashboardTickets"
    );

    vi.mock("@/components/dashboard/DashboardTickets.module.css", () => ({
      default: new Proxy({}, { get: (_t, prop: string) => prop }),
    }));

    render(
      <DashboardTickets
        initialTickets={[makeTicket({ conversation_id: "conv-99" })]}
        total={1}
        filter={{ category: null, sort: "priority" }}
        apiPath="/api/v1/tickets"
      />
    );

    fireEvent.click(screen.getByTestId("open-transcript-btn"));

    // Drawer appears
    expect(screen.getByTestId("transcript-drawer")).toBeInTheDocument();

    // Turn renders after fetch resolves
    await waitFor(() => {
      expect(screen.getByTestId("assistant-answer")).toBeInTheDocument();
    });

    // Close button removes drawer
    fireEvent.click(screen.getByTestId("close-drawer-btn"));
    expect(screen.queryByTestId("transcript-drawer")).toBeNull();
  });
});
