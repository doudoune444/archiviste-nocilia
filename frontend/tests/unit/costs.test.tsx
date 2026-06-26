// Render tests for the Coûts · 30 j card (#275, reshaped for #349 / PRD #346).
//
// The card is presentational, no fetch. Per the v03 mockup it leads with the
// period total then lists three service lines — « Workers (LLM Mistral) »,
// « PostgreSQL », « GCS » — each with a monospace amount (fr-FR euros) and a
// bar proportional to the total. An accessible info tooltip on the card title
// spells out the estimation methodology. Error state shows only a request id.
//
// Behaviour is verified through the public render output, never internal state.

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup, within, fireEvent } from "@testing-library/react";
import { CostsCard } from "@/components/costs-card/CostsCard";
import type { CostsResult } from "@/lib/observability-types";

const METHODOLOGY_LABEL = "Méthode d'estimation des coûts";
const METHODOLOGY_TEXT =
  "Estimation basée sur les tarifs publics GCP, hors crédits et remises.";

const WORKERS_LABEL = "Workers (LLM Mistral)";
const POSTGRES_LABEL = "PostgreSQL";
const GCS_LABEL = "GCS";

afterEach(() => {
  cleanup();
});

const OK_COSTS: CostsResult = {
  kind: "ok",
  currency: "EUR",
  period: "rolling_30d",
  estimated: true,
  total_eur: 4.82,
  services: { postgres: 2.1, gcs: 0.47, workers: 2.25 },
  computed_at: "2026-06-24T10:00:00+00:00",
};

describe("CostsCard — period total in head", () => {
  it("leads with the period total formatted as fr-FR euros", () => {
    render(<CostsCard costs={OK_COSTS} />);
    const card = screen.getByLabelText("Coûts");
    expect(within(card).getByText(/4,82\s*€/)).toBeInTheDocument();
  });

  it("labels the total as « total période » verbatim", () => {
    render(<CostsCard costs={OK_COSTS} />);
    expect(screen.getByText("total période")).toBeInTheDocument();
  });
});

describe("CostsCard — service lines (verbatim labels)", () => {
  it("renders the three service labels verbatim from the mockup", () => {
    render(<CostsCard costs={OK_COSTS} />);
    expect(screen.getByText(WORKERS_LABEL)).toBeInTheDocument();
    expect(screen.getByText(POSTGRES_LABEL)).toBeInTheDocument();
    expect(screen.getByText(GCS_LABEL)).toBeInTheDocument();
  });

  it("formats each service amount as fr-FR euros", () => {
    render(<CostsCard costs={OK_COSTS} />);
    const card = screen.getByLabelText("Coûts");
    expect(within(card).getByText(/2,10\s*€/)).toBeInTheDocument();
    expect(within(card).getByText(/0,47\s*€/)).toBeInTheDocument();
    expect(within(card).getByText(/2,25\s*€/)).toBeInTheDocument();
  });

  it("never renders a raw dot-decimal amount", () => {
    render(<CostsCard costs={OK_COSTS} />);
    expect(screen.queryByText("4.82")).not.toBeInTheDocument();
  });

  it("renders a bar per service whose width is proportional to the total", () => {
    render(<CostsCard costs={OK_COSTS} />);
    const card = screen.getByLabelText("Coûts");
    // Bars are exposed via progressbar role with value relative to the total.
    const bars = within(card).getAllByRole("progressbar");
    expect(bars).toHaveLength(3);

    const widthFor = (label: string) =>
      bars.find((bar) => bar.getAttribute("aria-label")?.includes(label));

    const workersBar = widthFor(WORKERS_LABEL);
    const postgresBar = widthFor(POSTGRES_LABEL);
    const gcsBar = widthFor(GCS_LABEL);
    expect(workersBar).toBeTruthy();
    expect(postgresBar).toBeTruthy();
    expect(gcsBar).toBeTruthy();

    // 2.25 / 4.82 ≈ 46.7 % of the total.
    expect(workersBar).toHaveAttribute("aria-valuemax", "4.82");
    expect(workersBar).toHaveAttribute("aria-valuenow", "2.25");
    expect(gcsBar).toHaveAttribute("aria-valuenow", "0.47");
  });
});

describe("CostsCard — estimation methodology tooltip (#349)", () => {
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

  it("opens the methodology text on hover and links it via aria-describedby", () => {
    render(<CostsCard costs={OK_COSTS} />);
    const trigger = screen.getByRole("button", { name: METHODOLOGY_LABEL });
    fireEvent.mouseEnter(trigger);

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

  it("omits the info icon on error", () => {
    const costs: CostsResult = { kind: "error", request_id: "req-cost-1" };
    render(<CostsCard costs={costs} />);
    expect(
      screen.queryByRole("button", { name: METHODOLOGY_LABEL })
    ).not.toBeInTheDocument();
  });
});

describe("CostsCard — error state", () => {
  it("renders only a request id and no amounts on error", () => {
    const costs: CostsResult = { kind: "error", request_id: "req-cost-9" };
    render(<CostsCard costs={costs} />);
    expect(screen.getByText(/req-cost-9/)).toBeInTheDocument();
    expect(screen.queryByText(/€/)).not.toBeInTheDocument();
    expect(screen.queryByText("total période")).not.toBeInTheDocument();
  });
});
