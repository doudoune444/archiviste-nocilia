/**
 * Métriques page shell & layout — issue #347 (PRD #346).
 *
 * Primary seam: the `/metriques` server component rendered with `next/headers`
 * and the BFF proxy (`forward`) mocked. We assert the externally observable
 * shell — the new eyebrow + h1, the four cards (by their accessible labels),
 * and the stack footer — never CSS classes or internal DOM structure.
 *
 * DepHealth is a client island that polls `/api/v1/status` on mount; we mock
 * global `fetch` so it lands in a deterministic rendered state.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import React from "react";

vi.mock("next/headers", () => ({
  headers: vi.fn().mockResolvedValue({ get: () => null }),
}));

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

// forward() is called once per server-side signal (stats, quality, costs).
// Route by the gateway path so each card receives a well-formed payload.
vi.mock("@/lib/bff-proxy", () => ({
  forward: vi.fn(async (_req: Request, path: string) => {
    if (path === "/v1/stats") return jsonResponse({ conversation_count: 1247 });
    if (path === "/v1/costs")
      return jsonResponse({
        total_eur: 4.82,
        services: { postgres: 2.1, gcs: 0.47, workers: 2.25 },
      });
    if (path === "/v1/quality")
      return jsonResponse({
        faithfulness: 0.91,
        answer_relevancy: 0.88,
        context_precision: 0.76,
        context_recall: 0.73,
        golden_set_version: "v3",
        finished_at: "2026-06-24T03:12:00+00:00",
      });
    return jsonResponse({});
  }),
}));

vi.mock("@/app/metriques/page.module.css", () => ({
  default: new Proxy({}, { get: (_t, prop: string) => prop }),
}));
vi.mock("@/components/info-tooltip/InfoTooltip.module.css", () => ({
  default: new Proxy({}, { get: (_t, prop: string) => prop }),
}));

const { default: MetriquesPage } = await import("@/app/metriques/page");

function statusBody(workersStatus: string) {
  return {
    status: "ok",
    dependencies: {
      postgres: { status: "ok", latency_ms: 3 },
      gcs: { status: "ok", latency_ms: 12 },
      workers: { status: workersStatus, latency_ms: 8 },
    },
    checked_at: "2026-06-24T10:00:00Z",
  };
}

async function renderPage(workersStatus = "dormant") {
  // DepHealth island fetches /api/v1/status on mount.
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => jsonResponse(statusBody(workersStatus)))
  );
  const element = await MetriquesPage();
  render(element);
}

describe("Métriques page shell (#347)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the new h1 « État et métriques »", async () => {
    await renderPage();
    expect(
      screen.getByRole("heading", { level: 1, name: "État et métriques" })
    ).toBeInTheDocument();
  });

  it("renders the eyebrow « Archiviste Nocilia · RAG public »", async () => {
    await renderPage();
    expect(
      screen.getByText("Archiviste Nocilia · RAG public")
    ).toBeInTheDocument();
  });

  it("no longer renders the former « Observabilité » heading", async () => {
    await renderPage();
    expect(screen.queryByText("Observabilité")).not.toBeInTheDocument();
  });

  it("slots the four existing cards in the band", async () => {
    await renderPage();
    // Cards are identified by their stable accessible labels, not CSS.
    expect(screen.getByLabelText("Qualité RAG")).toBeInTheDocument();
    expect(screen.getByLabelText("Coûts")).toBeInTheDocument();
    expect(screen.getByLabelText("Conversations")).toBeInTheDocument();
    expect(screen.getByLabelText("Dépendances")).toBeInTheDocument();
  });

  it("renders the stack footer line", async () => {
    await renderPage();
    expect(
      screen.getByText(
        /Gateway Rust \(Axum\) · Workers Python \(FastAPI \/ LangChain\) · Persistence Markdown sur GCS/
      )
    ).toBeInTheDocument();
  });
});

describe("Métriques page — Conversations & Dépendances cards (#350)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the conversation count and the « traitées au total » legend", async () => {
    await renderPage();
    expect(await screen.findByText("1247")).toBeInTheDocument();
    expect(screen.getByText("traitées au total")).toBeInTheDocument();
  });

  it("renders the dependency rows for PostgreSQL, GCS and Workers", async () => {
    await renderPage("ok");
    await screen.findByText("PostgreSQL");
    // « GCS » also appears in the footer; scope to the Dépendances card.
    const card = screen.getByLabelText("Dépendances");
    expect(within(card).getByText("PostgreSQL")).toBeInTheDocument();
    expect(within(card).getByText("GCS")).toBeInTheDocument();
    expect(within(card).getByText("Workers")).toBeInTheDocument();
    // All three operational → three « Opérationnel » status labels.
    expect(within(card).getAllByText("Opérationnel")).toHaveLength(3);
  });

  it("shows the dormant Workers state as « En veille » with the scale-to-zero hint", async () => {
    await renderPage("dormant");
    expect(await screen.findByText("En veille")).toBeInTheDocument();
    expect(screen.getByText(/démarrage à froid à la demande/)).toBeInTheDocument();
    // « En veille » is never the red down label.
    expect(screen.queryByText("Hors service")).not.toBeInTheDocument();
  });
});

describe("Métriques page — independent cards (#347 AC indépendance)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("keeps the shell and the other cards when a single signal fails", async () => {
    const { forward } = await import("@/lib/bff-proxy");
    (forward as ReturnType<typeof vi.fn>).mockImplementation(
      async (_req: Request, path: string) => {
        if (path === "/v1/stats")
          return new Response(JSON.stringify({ error: "internal" }), {
            status: 500,
            headers: {
              "content-type": "application/json",
              "x-request-id": "req-stats-fail",
            },
          });
        if (path === "/v1/costs")
          return jsonResponse({
            total_eur: 4.82,
            services: { postgres: 2.1, gcs: 0.47, workers: 2.25 },
          });
        return jsonResponse({
          faithfulness: 0.91,
          answer_relevancy: 0.88,
          context_precision: 0.76,
          context_recall: 0.73,
          golden_set_version: "v3",
          finished_at: "2026-06-24T03:12:00+00:00",
        });
      }
    );

    await renderPage();

    // The failing stats card surfaces its request id without leaking internals,
    // while the page shell and the other cards still render.
    expect(screen.getByText(/req-stats-fail/)).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 1, name: "État et métriques" })
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Qualité RAG")).toBeInTheDocument();
    expect(screen.getByLabelText("Coûts")).toBeInTheDocument();
  });
});

describe("Métriques error boundary (#347 — renamed shell)", () => {
  it("uses the new « État et métriques » heading, not « Observabilité »", async () => {
    const { default: MetriquesError } = await import("@/app/metriques/error");
    render(<MetriquesError />);
    expect(
      screen.getByRole("heading", { level: 1, name: "État et métriques" })
    ).toBeInTheDocument();
    expect(screen.queryByText("Observabilité")).not.toBeInTheDocument();
  });
});
