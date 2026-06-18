// Tests for WEBOBS-001 — public observability page
// AC1: Page renders server-side (RSC) via bff-proxy — tested by checking components render without client-side fetch
// AC2: stats card shows conversation count
// AC3: ragas-gauges renders FOUR scores with golden-set version and finished-at timestamp
// AC4: no_data shape renders clean empty state — never a broken page
// AC5: load failure shows error state with request id; independent cards

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatsCard } from "@/components/stats-card/StatsCard";
import { RagasGauges } from "@/components/ragas-gauges/RagasGauges";
import type { StatsResult, QualityResult } from "@/lib/observability-types";

// --- StatsCard ---

describe("StatsCard", () => {
  // AC2: stats card shows conversation count
  it("renders conversation count from stats payload", () => {
    const stats: StatsResult = { kind: "ok", conversation_count: 42 };
    render(<StatsCard stats={stats} />);
    expect(screen.getByText("42")).toBeInTheDocument();
  });

  // AC5: load failure shows error state with request id
  it("renders error state with request id when stats failed to load", () => {
    const stats: StatsResult = { kind: "error", request_id: "req-abc-123" };
    render(<StatsCard stats={stats} />);
    expect(screen.getByText(/req-abc-123/)).toBeInTheDocument();
    expect(screen.queryByText(/42/)).not.toBeInTheDocument();
  });
});

// --- RagasGauges ---

describe("RagasGauges", () => {
  // AC3: renders four scores as labeled gauges with golden-set version + finished-at timestamp
  it("renders all four gauges with labels and values when metrics available", () => {
    const quality: QualityResult = {
      kind: "ok",
      faithfulness: 0.87,
      answer_relevancy: 0.92,
      context_precision: 0.75,
      context_recall: 0.81,
      golden_set_version: "v1.2.3",
      finished_at: "2025-01-15T14:32:00Z",
    };
    render(<RagasGauges quality={quality} />);

    // four gauge labels must be present
    expect(screen.getByText(/faithfulness/i)).toBeInTheDocument();
    expect(screen.getByText(/answer.?relevancy/i)).toBeInTheDocument();
    expect(screen.getByText(/context.?precision/i)).toBeInTheDocument();
    expect(screen.getByText(/context.?recall/i)).toBeInTheDocument();

    // golden-set version and finished-at timestamp
    expect(screen.getByText(/v1\.2\.3/)).toBeInTheDocument();
    expect(screen.getByText(/2025-01-15/)).toBeInTheDocument();
  });

  // AC3: numeric scores rendered (checking a value)
  it("renders numeric score values for each gauge", () => {
    const quality: QualityResult = {
      kind: "ok",
      faithfulness: 0.87,
      answer_relevancy: 0.92,
      context_precision: 0.75,
      context_recall: 0.81,
      golden_set_version: "v1.0.0",
      finished_at: "2025-06-18T10:00:00Z",
    };
    render(<RagasGauges quality={quality} />);

    // Each score should appear somewhere in the rendered output
    expect(screen.getByText(/0\.87/)).toBeInTheDocument();
    expect(screen.getByText(/0\.92/)).toBeInTheDocument();
    expect(screen.getByText(/0\.75/)).toBeInTheDocument();
    expect(screen.getByText(/0\.81/)).toBeInTheDocument();
  });

  // AC4: no_data shape renders clean empty state — never a broken page
  it("renders empty state without gauges when no eval data", () => {
    const quality: QualityResult = { kind: "no_data" };
    render(<RagasGauges quality={quality} />);

    // No gauge labels
    expect(screen.queryByText(/faithfulness/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/answer.?relevancy/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/context.?precision/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/context.?recall/i)).not.toBeInTheDocument();

    // Shows an empty-state message
    expect(screen.getByText(/aucune/i)).toBeInTheDocument();
  });

  // AC5: load failure shows error state with request id
  it("renders error state with request id when quality fetch failed", () => {
    const quality: QualityResult = { kind: "error", request_id: "req-xyz-789" };
    render(<RagasGauges quality={quality} />);

    expect(screen.getByText(/req-xyz-789/)).toBeInTheDocument();
    // No gauges rendered
    expect(screen.queryByText(/faithfulness/i)).not.toBeInTheDocument();
  });
});
