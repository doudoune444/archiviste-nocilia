// Tests for the public observability page.
//
// StatsCard: WEBOBS-001 (unchanged).
// RagasGauges: issue #252 (Observabilité, Lot 1, slice 2) — the Qualité RAG
// card is made understandable to non-technical visitors. French labels for the
// four indicators, each with an info tooltip (slice-1 component); the last-eval
// date formatted in readable French (day/month/year, no time, Europe/Paris);
// the golden-set version hash removed from the display. Numeric 0–1 values and
// the no_data / error states are preserved.

import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
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

// --- RagasGauges (#252) ---

const OK_QUALITY: QualityResult = {
  kind: "ok",
  faithfulness: 0.87,
  answer_relevancy: 0.92,
  context_precision: 0.75,
  context_recall: 0.81,
  golden_set_version: "v1.2.3",
  // 23 June 2026, 14:32 UTC — still 23 June in Europe/Paris.
  finished_at: "2026-06-23T14:32:00Z",
};

const FR_LABELS = [
  "Fidélité",
  "Pertinence",
  "Précision du contexte",
  "Couverture du contexte",
] as const;

describe("RagasGauges", () => {
  // AC: the four French labels are present.
  it("renders the four French indicator labels", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    for (const label of FR_LABELS) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  // AC: numeric 0–1 values still displayed.
  it("renders the four numeric score values", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    expect(screen.getByText(/0\.87/)).toBeInTheDocument();
    expect(screen.getByText(/0\.92/)).toBeInTheDocument();
    expect(screen.getByText(/0\.75/)).toBeInTheDocument();
    expect(screen.getByText(/0\.81/)).toBeInTheDocument();
  });

  // AC: date renders in readable French day/month/year, no time, not raw ISO.
  it("renders the finished-at date in readable French (no time)", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    expect(screen.getByText("23 juin 2026")).toBeInTheDocument();
    // Raw ISO string must not leak.
    expect(screen.queryByText(/2026-06-23T14:32:00Z/)).not.toBeInTheDocument();
    // No time component.
    expect(screen.queryByText(/14:32/)).not.toBeInTheDocument();
  });

  // AC: version hash no longer appears anywhere in the card.
  it("does not render the golden-set version hash", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    expect(screen.queryByText(/v1\.2\.3/)).not.toBeInTheDocument();
  });

  // AC: each of the four indicators has an info tooltip with the validated copy
  // (technical name included), linked to its trigger via aria-describedby.
  it("exposes an info tooltip per indicator with technical name and explanation", () => {
    render(<RagasGauges quality={OK_QUALITY} />);

    const cases: Array<{ technicalName: string; explanationFragment: RegExp }> = [
      { technicalName: "faithfulness", explanationFragment: /colle-t-elle aux sources/i },
      { technicalName: "answer relevancy", explanationFragment: /répond-elle vraiment à la question/i },
      { technicalName: "context precision", explanationFragment: /placés en tête des sources/i },
      { technicalName: "context recall", explanationFragment: /toutes les sources nécessaires/i },
    ];

    for (const { technicalName, explanationFragment } of cases) {
      const trigger = screen.getByRole("button", {
        name: new RegExp(technicalName, "i"),
      });
      fireEvent.click(trigger);

      const describedById = trigger.getAttribute("aria-describedby");
      expect(describedById).toBeTruthy();

      const tooltip = document.getElementById(describedById as string);
      expect(tooltip).not.toBeNull();
      expect(tooltip?.textContent).toMatch(explanationFragment);
      expect(tooltip?.textContent).toMatch(new RegExp(technicalName, "i"));

      fireEvent.click(trigger);
    }
  });

  // AC: the date has its own info tooltip with the validated copy, linked via
  // aria-describedby.
  it("exposes an info tooltip for the last-evaluation date", () => {
    render(<RagasGauges quality={OK_QUALITY} />);

    const trigger = screen.getByRole("button", {
      name: /date.*derni.re .valuation/i,
    });
    fireEvent.click(trigger);

    const describedById = trigger.getAttribute("aria-describedby");
    expect(describedById).toBeTruthy();
    const tooltip = document.getElementById(describedById as string);
    expect(tooltip?.textContent).toMatch(
      /derni.re .valuation automatique de la qualit. du RAG/i,
    );
  });

  // AC: no_data shape renders clean empty state — never a broken page.
  it("renders empty state without gauges when no eval data", () => {
    const quality: QualityResult = { kind: "no_data" };
    render(<RagasGauges quality={quality} />);

    for (const label of FR_LABELS) {
      expect(screen.queryByText(label)).not.toBeInTheDocument();
    }
    expect(screen.getByText(/aucune/i)).toBeInTheDocument();
  });

  // AC: error state with request id preserved.
  it("renders error state with request id when quality fetch failed", () => {
    const quality: QualityResult = { kind: "error", request_id: "req-xyz-789" };
    render(<RagasGauges quality={quality} />);

    expect(screen.getByText(/req-xyz-789/)).toBeInTheDocument();
    for (const label of FR_LABELS) {
      expect(screen.queryByText(label)).not.toBeInTheDocument();
    }
  });
});
