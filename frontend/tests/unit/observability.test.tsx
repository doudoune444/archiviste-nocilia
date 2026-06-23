// Tests for the public observability page.
//
// Lot 1 (#246) rewrote the RagasGauges expectations:
//   - four French metric labels (Fidélité, Pertinence, Précision du contexte,
//     Couverture du contexte) instead of the English ones;
//   - the finished-at date rendered in a readable French format, no ISO;
//   - the golden_set_version hash no longer rendered;
//   - per-metric and per-date explanations reachable via info buttons;
//   - no_data / error states preserved; numeric 0..1 values preserved.
//
// StatsCard tests are unchanged (out of scope for Lot 1).

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { StatsCard } from "@/components/stats-card/StatsCard";
import { RagasGauges } from "@/components/ragas-gauges/RagasGauges";
import type { StatsResult, QualityResult } from "@/lib/observability-types";

afterEach(cleanup);

// --- StatsCard ---

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

// --- RagasGauges ---

const FULL_QUALITY: QualityResult = {
  kind: "ok",
  faithfulness: 0.87,
  answer_relevancy: 0.92,
  context_precision: 0.75,
  context_recall: 0.81,
  golden_set_version: "9f3a8c1b2d4e5f60718293a4b5c6d7e8",
  finished_at: "2026-06-23T00:01:40+00:00",
};

describe("RagasGauges", () => {
  it("renders the four French metric labels", () => {
    render(<RagasGauges quality={FULL_QUALITY} />);
    expect(screen.getByText("Fidélité")).toBeInTheDocument();
    expect(screen.getByText("Pertinence")).toBeInTheDocument();
    expect(screen.getByText("Précision du contexte")).toBeInTheDocument();
    expect(screen.getByText("Couverture du contexte")).toBeInTheDocument();
  });

  it("no longer renders the English labels", () => {
    render(<RagasGauges quality={FULL_QUALITY} />);
    expect(screen.queryByText("Faithfulness")).not.toBeInTheDocument();
    expect(screen.queryByText("Answer Relevancy")).not.toBeInTheDocument();
    expect(screen.queryByText("Context Precision")).not.toBeInTheDocument();
    expect(screen.queryByText("Context Recall")).not.toBeInTheDocument();
  });

  it("renders the finished-at date in a readable French format, not ISO", () => {
    render(<RagasGauges quality={FULL_QUALITY} />);
    expect(screen.getByText(/23 juin 2026/)).toBeInTheDocument();
    expect(screen.queryByText(/2026-06-23/)).not.toBeInTheDocument();
    expect(screen.queryByText(/00:01:40/)).not.toBeInTheDocument();
  });

  it("no longer renders the golden-set version hash", () => {
    render(<RagasGauges quality={FULL_QUALITY} />);
    expect(
      screen.queryByText(/9f3a8c1b2d4e5f60718293a4b5c6d7e8/)
    ).not.toBeInTheDocument();
  });

  it("keeps the numeric score values for each gauge", () => {
    render(<RagasGauges quality={FULL_QUALITY} />);
    expect(screen.getByText("0.87")).toBeInTheDocument();
    expect(screen.getByText("0.92")).toBeInTheDocument();
    expect(screen.getByText("0.75")).toBeInTheDocument();
    expect(screen.getByText("0.81")).toBeInTheDocument();
  });

  it("exposes each metric explanation through an info button, naming the original metric", () => {
    render(<RagasGauges quality={FULL_QUALITY} />);

    const faithfulnessButton = screen.getByRole("button", {
      name: /Fidélité/i,
    });
    fireEvent.click(faithfulnessButton);
    expect(
      screen.getByText(/colle-t-elle aux sources/i)
    ).toBeInTheDocument();
    expect(screen.getByText(/faithfulness/i)).toBeInTheDocument();
  });

  it("exposes a date explanation through an info button", () => {
    render(<RagasGauges quality={FULL_QUALITY} />);
    const dateButton = screen.getByRole("button", {
      name: /dernière évaluation/i,
    });
    fireEvent.click(dateButton);
    expect(
      screen.getByText(/dernière évaluation automatique de la qualité/i)
    ).toBeInTheDocument();
  });

  it("renders empty state without gauges when no eval data", () => {
    const quality: QualityResult = { kind: "no_data" };
    render(<RagasGauges quality={quality} />);

    expect(screen.queryByText("Fidélité")).not.toBeInTheDocument();
    expect(screen.queryByText("Pertinence")).not.toBeInTheDocument();
    expect(screen.getByText(/aucune/i)).toBeInTheDocument();
  });

  it("renders error state with request id when quality fetch failed", () => {
    const quality: QualityResult = { kind: "error", request_id: "req-xyz-789" };
    render(<RagasGauges quality={quality} />);

    expect(screen.getByText(/req-xyz-789/)).toBeInTheDocument();
    expect(screen.queryByText("Fidélité")).not.toBeInTheDocument();
  });
});
