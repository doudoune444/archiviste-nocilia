// Render tests for the Coûts · 30 j card (#275, reworked for #349 / PRD #346).
//
// The card is presentational, no fetch. Per the v03 mockup it leads with the
// period total, then lists the three service lines — « Workers (LLM Mistral) »,
// « PostgreSQL », « GCS » — each with a monospace amount and a bar proportional
// to the total. A title tooltip spells out the estimation methodology,
// accessibly (hover AND keyboard focus). Amounts are formatted in fr-FR euros
// (« 12,34 € »). Error state shows a request id and no amounts.
//
// Behaviour is verified through the public render output, never internal state.

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup, within, fireEvent } from "@testing-library/react";
import { CostsCard } from "@/components/costs-card/CostsCard";
import type { CostsResult } from "@/lib/observability-types";

const METHODOLOGY_LABEL = "Méthode d'estimation des coûts";
const METHODOLOGY_TEXT =
  "Estimation basée sur les tarifs publics GCP, hors crédits et remises.";

afterEach(() => {
  cleanup();
});

const OK_COSTS: CostsResult = {
  kind: "ok",
  currency: "EUR",
  period: "rolling_30d",
  estimated: true,
  total_eur: 12.34,
  services: { postgres: 8.0, gcs: 0.5, workers: 3.84 },
  computed_at: "2026-06-24T10:00:00+00:00",
};

describe("CostsCard — period total in head", () => {
  it("renders the period total as fr-FR euros at the head of the card", () => {
    render(<CostsCard costs={OK_COSTS} />);
    const card = screen.getByLabelText("Coûts");
    expect(within(card).getByText(/12,34\s*€/)).toBeInTheDocument();
  });

  it("never renders a raw dot-decimal amount", () => {
    render(<CostsCard costs={OK_COSTS} />);
    expect(screen.queryByText("12.34")).not.toBeInTheDocument();
  });
});

describe("CostsCard — per-service lines (verbatim labels)", () => {
  it("renders the three service labels verbatim from the mockup", () => {
    render(<CostsCard costs={OK_COSTS} />);
    expect(screen.getByText("Workers (LLM Mistral)")).toBeInTheDocument();
    expect(screen.getByText("PostgreSQL")).toBeInTheDocument();
    expect(screen.getByText("GCS")).toBeInTheDocument();
  });

  it("formats each service amount as fr-FR euros", () => {
    render(<CostsCard costs={OK_COSTS} />);
    const card = screen.getByLabelText("Coûts");
    expect(within(card).getByText(/8,00\s*€/)).toBeInTheDocument();
    expect(within(card).getByText(/0,50\s*€/)).toBeInTheDocument();
    expect(within(card).getByText(/3,84\s*€/)).toBeInTheDocument();
  });

  it("renders each service amount in the monospace font", () => {
    render(<CostsCard costs={OK_COSTS} />);
    const amount = screen.getByText(/8,00\s*€/);
    expect(amount.className).toMatch(/amount/);
  });

  it("draws a per-service bar whose width is proportional to the total", () => {
    render(<CostsCard costs={OK_COSTS} />);
    // postgres 8.00 / total 12.34 ≈ 64.8 %
    const bars = screen.getAllByRole("meter");
    expect(bars).toHaveLength(3);
    const postgresBar = bars.find(
      (bar) => bar.getAttribute("aria-valuenow") === "8"
    );
    expect(postgresBar).toBeDefined();
    expect(postgresBar).toHaveAttribute("aria-valuemax", "12.34");
    const fill = postgresBar?.firstElementChild as HTMLElement;
    expect(fill.style.width).toBe("64.8%");
  });
});

describe("CostsCard — estimation methodology tooltip", () => {
  it("renders the methodology info icon as a button with an accessible label", () => {
    render(<CostsCard costs={OK_COSTS} />);
    const trigger = screen.getByRole("button", { name: METHODOLOGY_LABEL });
    expect(trigger.tagName).toBe("BUTTON");
    expect(trigger).toHaveAttribute("aria-label", METHODOLOGY_LABEL);
  });

  it("keeps the methodology text closed until the icon is activated", () => {
    render(<CostsCard costs={OK_COSTS} />);
    expect(screen.queryByText(METHODOLOGY_TEXT)).not.toBeInTheDocument();
  });

  it("opens the methodology text on tap/click and links it via aria-describedby", () => {
    render(<CostsCard costs={OK_COSTS} />);
    const trigger = screen.getByRole("button", { name: METHODOLOGY_LABEL });
    fireEvent.click(trigger);

    expect(screen.getByText(METHODOLOGY_TEXT)).toBeInTheDocument();
    const describedBy = trigger.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    const tooltip = document.getElementById(describedBy as string);
    expect(tooltip).toHaveTextContent(METHODOLOGY_TEXT);
  });

  it("opens the methodology text on keyboard focus", () => {
    render(<CostsCard costs={OK_COSTS} />);
    const trigger = screen.getByRole("button", { name: METHODOLOGY_LABEL });
    fireEvent.focus(trigger);
    expect(screen.getByText(METHODOLOGY_TEXT)).toBeInTheDocument();
  });
});

describe("CostsCard — error state", () => {
  it("renders the request id and no amounts on error", () => {
    const costs: CostsResult = { kind: "error", request_id: "req-cost-9" };
    render(<CostsCard costs={costs} />);
    expect(screen.getByText(/req-cost-9/)).toBeInTheDocument();
    expect(screen.queryByText(/€/)).not.toBeInTheDocument();
  });

  it("omits the methodology tooltip and service bars on error", () => {
    const costs: CostsResult = { kind: "error", request_id: "req-cost-1" };
    render(<CostsCard costs={costs} />);
    expect(
      screen.queryByRole("button", { name: METHODOLOGY_LABEL })
    ).not.toBeInTheDocument();
    expect(screen.queryAllByRole("meter")).toHaveLength(0);
  });
});
