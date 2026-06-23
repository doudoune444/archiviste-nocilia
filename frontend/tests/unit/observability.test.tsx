// Tests for the observability page cards.
//
// StatsCard (WEBOBS-001) is unchanged. RagasGauges is reworked for issue #252
// (Observabilité — Qualité RAG readable for non-technical visitors):
//   - French labels for the four indicators (Fidélité, Pertinence,
//     Précision du contexte, Couverture du contexte)
//   - each indicator + the date carry an InfoTooltip (slice 1) with validated
//     copy including the technical name
//   - finished_at rendered as readable French day/month/year (no time), not ISO
//   - golden_set_version no longer rendered anywhere
//   - numeric 0..1 values still shown
//   - no_data + error (with request id) states preserved
//
// Behaviour is verified through the public render output, never internal state.

import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup, within } from "@testing-library/react";
import { StatsCard } from "@/components/stats-card/StatsCard";
import { RagasGauges } from "@/components/ragas-gauges/RagasGauges";
import type { StatsResult, QualityResult } from "@/lib/observability-types";

// jsdom cannot process real CSS modules; stub to identity proxy.
vi.mock("@/components/info-tooltip/InfoTooltip.module.css", () => ({
  default: new Proxy({}, { get: (_t, prop: string) => prop }),
}));

afterEach(() => {
  cleanup();
});

// --- StatsCard (unchanged) ---

describe("StatsCard", () => {
  it("renders conversation count from stats payload", () => {
    const stats: StatsResult = { kind: "ok", conversation_count: 42 };
    render(<StatsCard stats={stats} />);
    expect(screen.getByText("42")).toBeInTheDocument();
  });

  it("renders error state with request id when stats failed to load", () => {
    const stats: StatsResult = { kind: "error", request_id: "req-abc-123" };
    render(<StatsCard stats={stats} />);
    expect(screen.getByText(/req-abc-123/)).toBeInTheDocument();
    expect(screen.queryByText(/42/)).not.toBeInTheDocument();
  });
});

// --- RagasGauges (issue #252) ---

const OK_QUALITY: QualityResult = {
  kind: "ok",
  faithfulness: 0.87,
  answer_relevancy: 0.92,
  context_precision: 0.75,
  context_recall: 0.81,
  golden_set_version: "v1.2.3",
  finished_at: "2026-06-23T00:01:40+00:00",
};

describe("RagasGauges — French labels", () => {
  it("renders the four French indicator labels", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    expect(screen.getByText("Fidélité")).toBeInTheDocument();
    expect(screen.getByText("Pertinence")).toBeInTheDocument();
    expect(screen.getByText("Précision du contexte")).toBeInTheDocument();
    expect(screen.getByText("Couverture du contexte")).toBeInTheDocument();
  });

  it("no longer renders the old English labels", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    expect(screen.queryByText("Faithfulness")).not.toBeInTheDocument();
    expect(screen.queryByText("Answer Relevancy")).not.toBeInTheDocument();
    expect(screen.queryByText("Context Precision")).not.toBeInTheDocument();
    expect(screen.queryByText("Context Recall")).not.toBeInTheDocument();
  });
});

describe("RagasGauges — numeric values preserved", () => {
  it("renders the four numeric 0..1 values", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    expect(screen.getByText("0.87")).toBeInTheDocument();
    expect(screen.getByText("0.92")).toBeInTheDocument();
    expect(screen.getByText("0.75")).toBeInTheDocument();
    expect(screen.getByText("0.81")).toBeInTheDocument();
  });
});

describe("RagasGauges — readable French date", () => {
  it("renders finished_at as French day/month/year without time", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    expect(screen.getByText("23 juin 2026")).toBeInTheDocument();
  });

  it("does not render the raw ISO timestamp", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    expect(screen.queryByText(/2026-06-23T/)).not.toBeInTheDocument();
    expect(screen.queryByText(/00:01:40/)).not.toBeInTheDocument();
  });

  it("formats with the Europe/Paris timezone (date does not roll back across midnight UTC)", () => {
    // 22:30 UTC on 22 June is already 23 June in Europe/Paris (UTC+2 in summer).
    const lateEvening: QualityResult = {
      ...OK_QUALITY,
      finished_at: "2026-06-22T22:30:00+00:00",
    };
    render(<RagasGauges quality={lateEvening} />);
    expect(screen.getByText("23 juin 2026")).toBeInTheDocument();
  });
});

describe("RagasGauges — version hash removed", () => {
  it("never renders the golden_set_version hash", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    expect(screen.queryByText(/v1\.2\.3/)).not.toBeInTheDocument();
  });
});

describe("RagasGauges — info tooltips", () => {
  it("exposes an info tooltip trigger for each indicator and the date", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    // four indicators + one date = five triggers
    const triggers = screen.getAllByRole("button");
    expect(triggers).toHaveLength(5);
  });

  it("reveals the Fidélité explanation with its technical name on click", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    const trigger = screen.getByRole("button", { name: /fidélité/i });
    fireEvent.click(trigger);

    const describedBy = trigger.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    const tooltip = document.getElementById(describedBy as string);
    expect(tooltip).toHaveTextContent("Fidélité (faithfulness)");
    expect(tooltip).toHaveTextContent(
      "La réponse colle-t-elle aux sources récupérées, sans rien inventer ?"
    );
  });

  it("reveals the Pertinence explanation with its technical name on click", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    const trigger = screen.getByRole("button", { name: /pertinence/i });
    fireEvent.click(trigger);
    const tooltip = document.getElementById(
      trigger.getAttribute("aria-describedby") as string
    );
    expect(tooltip).toHaveTextContent("Pertinence (answer relevancy)");
    expect(tooltip).toHaveTextContent(
      "La réponse répond-elle vraiment à la question posée ?"
    );
  });

  it("reveals the Précision du contexte explanation with its technical name on click", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    const trigger = screen.getByRole("button", { name: /précision du contexte/i });
    fireEvent.click(trigger);
    const tooltip = document.getElementById(
      trigger.getAttribute("aria-describedby") as string
    );
    expect(tooltip).toHaveTextContent("Précision du contexte (context precision)");
    expect(tooltip).toHaveTextContent(
      "Les passages utiles sont-ils placés en tête des sources récupérées ?"
    );
  });

  it("reveals the Couverture du contexte explanation with its technical name on click", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    const trigger = screen.getByRole("button", { name: /couverture du contexte/i });
    fireEvent.click(trigger);
    const tooltip = document.getElementById(
      trigger.getAttribute("aria-describedby") as string
    );
    expect(tooltip).toHaveTextContent("Couverture du contexte (context recall)");
    expect(tooltip).toHaveTextContent(
      "A-t-on récupéré toutes les sources nécessaires pour répondre ?"
    );
  });

  it("reveals the date explanation on click", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    const trigger = screen.getByRole("button", { name: /date/i });
    fireEvent.click(trigger);
    const tooltip = document.getElementById(
      trigger.getAttribute("aria-describedby") as string
    );
    expect(tooltip).toHaveTextContent(
      "Date de la dernière évaluation automatique de la qualité du RAG."
    );
  });
});

describe("RagasGauges — preserved states", () => {
  it("renders a clean empty state without gauges for no_data", () => {
    render(<RagasGauges quality={{ kind: "no_data" }} />);
    expect(screen.queryByText("Fidélité")).not.toBeInTheDocument();
    expect(screen.queryByText("Pertinence")).not.toBeInTheDocument();
    expect(screen.getByText(/aucune/i)).toBeInTheDocument();
  });

  it("renders the error state with the request id and no gauges", () => {
    render(<RagasGauges quality={{ kind: "error", request_id: "req-xyz-789" }} />);
    expect(screen.getByText(/req-xyz-789/)).toBeInTheDocument();
    expect(screen.queryByText("Fidélité")).not.toBeInTheDocument();
  });
});

describe("RagasGauges — tooltip linkage", () => {
  it("links each opened tooltip to its trigger via aria-describedby", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    const card = screen.getByLabelText("Qualité RAG");
    const trigger = within(card).getByRole("button", { name: /fidélité/i });
    fireEvent.click(trigger);

    const describedBy = trigger.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy as string)).not.toBeNull();
  });
});
