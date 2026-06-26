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
import { render, screen, fireEvent, within } from "@testing-library/react";
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

async function renderPage() {
  // DepHealth island fetches /api/v1/status on mount; the gateway body nests
  // the three dependencies (parse-status.ts contract).
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      jsonResponse({
        status: "ok",
        dependencies: {
          postgres: { status: "ok", latency_ms: 3 },
          gcs: { status: "ok", latency_ms: 12 },
          workers: { status: "ok", latency_ms: 8 },
        },
        checked_at: "2026-06-24T03:12:00+00:00",
      })
    )
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

describe("Métriques page — Décisions section (#351)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the « Décisions & pistes d'amélioration » heading and subtitle", async () => {
    await renderPage();
    expect(
      screen.getByRole("heading", {
        level: 2,
        name: "Décisions & pistes d'amélioration",
      })
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Les décisions sont adaptées à mon besoin et à mes contraintes ; de meilleures solutions restent envisageables."
      )
    ).toBeInTheDocument();
  });

  it("renders the five decision cards with their prose and improvement insets", async () => {
    await renderPage();
    const cards = screen.getAllByRole("listitem");
    expect(cards).toHaveLength(5);
    expect(
      screen.getByRole("heading", { level: 3, name: "Deux appels LLM en série" })
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Chaque requête passe par deux appels au même modèle/)
    ).toBeInTheDocument();
    expect(screen.getAllByText("Piste d'amélioration")).toHaveLength(5);
  });
});

describe("Métriques page — Qualité · Ragas card (#348)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the four Ragas indicator labels", async () => {
    await renderPage();
    expect(screen.getByText("Fidélité")).toBeInTheDocument();
    expect(screen.getByText("Pertinence")).toBeInTheDocument();
    expect(screen.getByText("Précision du contexte")).toBeInTheDocument();
    expect(screen.getByText("Couverture du contexte")).toBeInTheDocument();
  });

  it("renders the threshold legend verbatim", async () => {
    await renderPage();
    expect(screen.getByText("≥ 0.85 bon")).toBeInTheDocument();
    expect(screen.getByText("0.70–0.85 correct")).toBeInTheDocument();
    expect(screen.getByText("< 0.70 faible")).toBeInTheDocument();
  });

  it("renders the golden set version and last-eval date from the payload", async () => {
    await renderPage();
    expect(screen.getByText("v3")).toBeInTheDocument();
    expect(screen.getByText("24 juin 2026")).toBeInTheDocument();
  });

  it("exposes an indicator tooltip whose prose names the metric", async () => {
    await renderPage();
    const trigger = screen.getByRole("button", { name: /fidélité/i });
    fireEvent.focus(trigger);
    const tooltip = document.getElementById(
      trigger.getAttribute("aria-describedby") as string
    );
    expect(tooltip).toHaveTextContent("Fidélité (faithfulness)");
    expect(tooltip).toHaveTextContent(
      "La réponse colle-t-elle aux sources récupérées, sans rien inventer ?"
    );
  });
});

describe("Métriques page — Conversations & Dépendances cards (#350)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("asserts the conversations count and the dependency rows at the page seam", async () => {
    await renderPage();
    // Conversations hero number from /v1/stats.
    expect(screen.getByText("1247")).toBeInTheDocument();
    expect(screen.getByText("traitées au total")).toBeInTheDocument();
    // Dependency rows from the polled /api/v1/status island, scoped to the
    // Dépendances card (PostgreSQL/GCS labels also appear in the Coûts card).
    const depsCard = screen.getByLabelText("Dépendances");
    expect(await within(depsCard).findByText("PostgreSQL")).toBeInTheDocument();
    expect(within(depsCard).getByText("GCS")).toBeInTheDocument();
    expect(within(depsCard).getByText("Workers")).toBeInTheDocument();
  });

  it("renders the dormant Workers hint and a healthy (non-red) accessible label", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          status: "ok",
          dependencies: {
            postgres: { status: "ok", latency_ms: 3 },
            gcs: { status: "ok", latency_ms: 12 },
            workers: { status: "dormant", latency_ms: 8 },
          },
          checked_at: "2026-06-24T03:12:00+00:00",
        })
      )
    );
    const element = await MetriquesPage();
    render(element);

    expect(await screen.findByLabelText("Workers en veille")).toBeInTheDocument();
    expect(
      screen.queryByLabelText(/workers hors service/i)
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "Workers en scale-to-zero : démarrage à froid à la demande."
      )
    ).toBeInTheDocument();
  });
});

describe("Métriques page — Coûts · 30 j card (#349)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("asserts the per-service cost lines from the payload", async () => {
    await renderPage();
    const card = screen.getByLabelText("Coûts");
    expect(within(card).getByText("Workers (LLM Mistral)")).toBeInTheDocument();
    expect(within(card).getByText("PostgreSQL")).toBeInTheDocument();
    expect(within(card).getByText("GCS")).toBeInTheDocument();
  });

  it("renders the period total in the card head", async () => {
    await renderPage();
    const card = screen.getByLabelText("Coûts");
    expect(within(card).getByText(/4,82\s*€/)).toBeInTheDocument();
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
