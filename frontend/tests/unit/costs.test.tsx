// Render tests for the Coûts card (#275).
//
// The card mirrors StatsCard: presentational, no fetch. It renders the three
// service lines (Postgres / GCS / Workers) plus a total, all formatted in
// fr-FR euros (« 12,34 € »). Error state shows a request id, no amounts.
//
// Behaviour is verified through the public render output, never internal state.

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup, within, fireEvent } from "@testing-library/react";
import { CostsCard } from "@/components/costs-card/CostsCard";
import type { CostsResult } from "@/lib/observability-types";

const ESTIMATE_BADGE = "Estimation";
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

describe("CostsCard — service lines", () => {
  it("renders the three service labels", () => {
    render(<CostsCard costs={OK_COSTS} />);
    expect(screen.getByText("Postgres")).toBeInTheDocument();
    expect(screen.getByText("GCS")).toBeInTheDocument();
    expect(screen.getByText("Workers")).toBeInTheDocument();
  });

  it("renders a total line", () => {
    render(<CostsCard costs={OK_COSTS} />);
    expect(screen.getByText(/total/i)).toBeInTheDocument();
  });
});

describe("CostsCard — fr-FR euro formatting", () => {
  it("formats the total as fr-FR euros", () => {
    render(<CostsCard costs={OK_COSTS} />);
    // fr-FR: comma decimal separator, € suffix. NBSP between number and symbol.
    const card = screen.getByLabelText("Coûts");
    expect(within(card).getByText(/12,34\s*€/)).toBeInTheDocument();
  });

  it("formats each service amount as fr-FR euros", () => {
    render(<CostsCard costs={OK_COSTS} />);
    const card = screen.getByLabelText("Coûts");
    expect(within(card).getByText(/8,00\s*€/)).toBeInTheDocument();
    expect(within(card).getByText(/0,50\s*€/)).toBeInTheDocument();
    expect(within(card).getByText(/3,84\s*€/)).toBeInTheDocument();
  });

  it("never renders a raw dot-decimal amount", () => {
    render(<CostsCard costs={OK_COSTS} />);
    expect(screen.queryByText("12.34")).not.toBeInTheDocument();
  });
});

describe("CostsCard — estimate honesty layer (#276)", () => {
  it("shows an « Estimation » badge", () => {
    render(<CostsCard costs={OK_COSTS} />);
    const card = screen.getByLabelText("Coûts");
    expect(within(card).getByText(ESTIMATE_BADGE)).toBeInTheDocument();
  });

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

  it("omits the badge and info icon on error", () => {
    const costs: CostsResult = { kind: "error", request_id: "req-cost-1" };
    render(<CostsCard costs={costs} />);
    expect(screen.queryByText(ESTIMATE_BADGE)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: METHODOLOGY_LABEL })
    ).not.toBeInTheDocument();
  });
});

describe("CostsCard — error state", () => {
  it("renders the request id and no amounts on error", () => {
    const costs: CostsResult = { kind: "error", request_id: "req-cost-9" };
    render(<CostsCard costs={costs} />);
    expect(screen.getByText(/req-cost-9/)).toBeInTheDocument();
    expect(screen.queryByText(/€/)).not.toBeInTheDocument();
  });
});
