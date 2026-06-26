/**
 * Conversations card (StatsCard) — issue #350 (PRD #346).
 *
 * Seam: the presentational card rendered with a StatsResult. We assert the
 * externally observable behaviour from the mockup verbatim — the hero number,
 * the "traitées au total" legend, the accessible info tooltip — never CSS
 * classes or internal DOM structure. Prior art: dep-health-dormant.test.tsx.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup, within } from "@testing-library/react";
import { StatsCard } from "@/components/stats-card/StatsCard";

vi.mock("@/components/stats-card/StatsCard.module.css", () => ({
  default: new Proxy({}, { get: (_t, prop: string) => prop }),
}));
vi.mock("@/components/info-tooltip/InfoTooltip.module.css", () => ({
  default: new Proxy({}, { get: (_t, prop: string) => prop }),
}));

afterEach(() => {
  cleanup();
});

describe("Conversations card (#350)", () => {
  it("renders the hero conversation count from the payload", () => {
    render(<StatsCard stats={{ kind: "ok", conversation_count: 1247 }} />);
    expect(screen.getByText("1247")).toBeInTheDocument();
  });

  it("renders the « traitées au total » legend verbatim", () => {
    render(<StatsCard stats={{ kind: "ok", conversation_count: 1247 }} />);
    expect(screen.getByText("traitées au total")).toBeInTheDocument();
  });

  it("exposes an info tooltip reachable on keyboard focus with the verbatim prose", () => {
    render(<StatsCard stats={{ kind: "ok", conversation_count: 1247 }} />);
    const trigger = screen.getByRole("button", { name: /conversations/i });
    fireEvent.focus(trigger);
    const tooltip = screen.getByRole("tooltip");
    expect(
      within(tooltip).getByText(
        "Nombre total de conversations traitées par l'assistant."
      )
    ).toBeInTheDocument();
  });

  it("surfaces a request id without leaking internals when the signal fails", () => {
    render(<StatsCard stats={{ kind: "error", request_id: "req-conv-99" }} />);
    expect(screen.getByText(/req-conv-99/)).toBeInTheDocument();
    expect(screen.queryByText("traitées au total")).not.toBeInTheDocument();
  });

  it("keeps the « Conversations » accessible label on the card", () => {
    render(<StatsCard stats={{ kind: "ok", conversation_count: 5 }} />);
    expect(screen.getByLabelText("Conversations")).toBeInTheDocument();
  });
});
