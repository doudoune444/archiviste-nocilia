// Conversations card — issue #350 (PRD #346).
//
// The bottom-right "Conversations" card is reshaped from the v03 mockup:
//   - hero number (centred) from GET /v1/stats conversation_count
//   - the legend « traitées au total »
//   - an accessible info tooltip with the verbatim copy
//     « Nombre total de conversations traitées par l'assistant. »
//   - the card is now labelled « Conversations » (was « Statistiques »)
//   - the error state preserves the request id without leaking internals.
//
// Behaviour is verified through the public render output (text/roles), never
// CSS classes or internal DOM structure.

import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { StatsCard } from "@/components/stats-card/StatsCard";
import type { StatsResult } from "@/lib/observability-types";

vi.mock("@/components/info-tooltip/InfoTooltip.module.css", () => ({
  default: new Proxy({}, { get: (_t, prop: string) => prop }),
}));

afterEach(() => {
  cleanup();
});

describe("Conversations card (#350)", () => {
  it("renders the hero conversation count and the « traitées au total » legend", () => {
    const stats: StatsResult = { kind: "ok", conversation_count: 1247 };
    render(<StatsCard stats={stats} />);

    expect(screen.getByText("1247")).toBeInTheDocument();
    expect(screen.getByText("traitées au total")).toBeInTheDocument();
  });

  it("labels the card « Conversations », not « Statistiques »", () => {
    const stats: StatsResult = { kind: "ok", conversation_count: 3 };
    render(<StatsCard stats={stats} />);

    expect(screen.getByLabelText("Conversations")).toBeInTheDocument();
    expect(screen.queryByLabelText("Statistiques")).not.toBeInTheDocument();
  });

  it("exposes an accessible info tooltip with the verbatim explanation", () => {
    const stats: StatsResult = { kind: "ok", conversation_count: 3 };
    render(<StatsCard stats={stats} />);

    const trigger = screen.getByRole("button");
    fireEvent.click(trigger);

    const describedBy = trigger.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    const tooltip = document.getElementById(describedBy as string);
    expect(tooltip).toHaveTextContent(
      "Nombre total de conversations traitées par l'assistant."
    );
  });

  it("renders the error state with the request id and no hero number", () => {
    const stats: StatsResult = { kind: "error", request_id: "req-abc-123" };
    render(<StatsCard stats={stats} />);

    expect(screen.getByText(/req-abc-123/)).toBeInTheDocument();
    expect(screen.queryByText("traitées au total")).not.toBeInTheDocument();
  });
});
