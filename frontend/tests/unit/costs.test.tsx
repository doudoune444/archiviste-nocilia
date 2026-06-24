// Render tests for the Coûts card (#275).
//
// The card mirrors StatsCard: presentational, no fetch. It renders the three
// service lines (Postgres / GCS / Workers) plus a total, all formatted in
// fr-FR euros (« 12,34 € »). Error state shows a request id, no amounts.
//
// Behaviour is verified through the public render output, never internal state.

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup, within } from "@testing-library/react";
import { CostsCard } from "@/components/costs-card/CostsCard";
import type { CostsResult } from "@/lib/observability-types";

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

describe("CostsCard — error state", () => {
  it("renders the request id and no amounts on error", () => {
    const costs: CostsResult = { kind: "error", request_id: "req-cost-9" };
    render(<CostsCard costs={costs} />);
    expect(screen.getByText(/req-cost-9/)).toBeInTheDocument();
    expect(screen.queryByText(/€/)).not.toBeInTheDocument();
  });
});
