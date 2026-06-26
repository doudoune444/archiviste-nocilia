// Tests for the reworked Qualité · Ragas card — issue #348 (PRD #346).
//
// Behaviour verified through the public render output (text + roles), never CSS
// classes or internal DOM. The band→colour rule itself is unit-tested separately
// in ragas-bands.test.ts; here we assert the card consumes it (the displayed
// value and the bar carry the band on the data-* attribute exposed for styling).
//
// AC covered:
//   - 4 rows: name, monospace coloured value, coloured bar per band
//   - legend of the 3 thresholds with the right colours
//   - meta: golden set version + last-eval date, with an accessible tooltip
//   - no_data state distinct from error (cold start)
//   - error state shows only a request id, no internal leak
//   - tooltips reachable on hover AND keyboard focus (role=tooltip, aria-label)

import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup, within } from "@testing-library/react";
import { RagasGauges } from "@/components/ragas-gauges/RagasGauges";
import type { QualityResult } from "@/lib/observability-types";

vi.mock("@/components/info-tooltip/InfoTooltip.module.css", () => ({
  default: new Proxy({}, { get: (_t, prop: string) => prop }),
}));
vi.mock("@/components/ragas-gauges/RagasGauges.module.css", () => ({
  default: new Proxy({}, { get: (_t, prop: string) => prop }),
}));

afterEach(() => {
  cleanup();
});

// faithfulness good (≥0.85), answer_relevancy good, context_precision fair
// (0.70–0.85), context_recall fair — and one value pushed to weak in a variant.
const OK_QUALITY: QualityResult = {
  kind: "ok",
  faithfulness: 0.91,
  answer_relevancy: 0.88,
  context_precision: 0.76,
  context_recall: 0.73,
  golden_set_version: "v3",
  finished_at: "2026-06-24T03:12:00+00:00",
};

function valueElement(text: string): HTMLElement {
  return screen.getByText(text);
}

describe("Ragas card — four rows with name, value and bar", () => {
  it("renders the four French indicator labels", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    expect(screen.getByText("Fidélité")).toBeInTheDocument();
    expect(screen.getByText("Pertinence")).toBeInTheDocument();
    expect(screen.getByText("Précision du contexte")).toBeInTheDocument();
    expect(screen.getByText("Couverture du contexte")).toBeInTheDocument();
  });

  it("renders each score as a two-decimal value", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    expect(screen.getByText("0.91")).toBeInTheDocument();
    expect(screen.getByText("0.88")).toBeInTheDocument();
    expect(screen.getByText("0.76")).toBeInTheDocument();
    expect(screen.getByText("0.73")).toBeInTheDocument();
  });

  it("exposes a meter bar per indicator with the raw 0..1 score", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    const meters = screen.getAllByRole("meter");
    expect(meters).toHaveLength(4);
    expect(meters[0]).toHaveAttribute("aria-valuenow", "0.91");
  });

  it("ships each bar width in a nonce-tagged style, never an inline attribute", () => {
    render(<RagasGauges quality={OK_QUALITY} nonce="ragas-nonce" />);
    const fill = screen
      .getAllByRole("meter")[0]
      .firstElementChild as HTMLElement;
    expect(fill.style.width).toBe("");
    const styleTag = document.querySelector("style");
    expect(styleTag).toHaveAttribute("nonce", "ragas-nonce");
    expect(styleTag?.textContent).toContain(`#${fill.id}{width:91%}`);
  });
});

describe("Ragas card — value/bar carry the quality band", () => {
  it("tags a ≥0.85 value as the good band", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    expect(valueElement("0.91")).toHaveAttribute("data-band", "good");
  });

  it("tags a 0.70–0.85 value as the fair band", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    expect(valueElement("0.76")).toHaveAttribute("data-band", "fair");
  });

  it("tags a <0.70 value as the weak band", () => {
    const weak: QualityResult = { ...OK_QUALITY, faithfulness: 0.42 };
    render(<RagasGauges quality={weak} />);
    expect(valueElement("0.42")).toHaveAttribute("data-band", "weak");
  });

  it("tags the meter bar with the same band as the value", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    const meters = screen.getAllByRole("meter");
    expect(meters[0]).toHaveAttribute("data-band", "good");
    expect(meters[2]).toHaveAttribute("data-band", "fair");
  });
});

describe("Ragas card — threshold legend", () => {
  it("renders the three threshold legend entries verbatim", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    expect(screen.getByText("≥ 0.85 bon")).toBeInTheDocument();
    expect(screen.getByText("0.70–0.85 correct")).toBeInTheDocument();
    expect(screen.getByText("< 0.70 faible")).toBeInTheDocument();
  });
});

describe("Ragas card — golden set + last-eval meta", () => {
  it("renders the golden set version from the payload", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    expect(screen.getByText(/Golden set/)).toBeInTheDocument();
    expect(screen.getByText("v3")).toBeInTheDocument();
  });

  it("renders the last-eval date as readable French day/month/year", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    expect(screen.getByText(/Dernière éval/)).toBeInTheDocument();
    expect(screen.getByText("24 juin 2026")).toBeInTheDocument();
  });

  it("exposes the date tooltip on keyboard focus", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    const trigger = screen.getByRole("button", { name: /date/i });
    fireEvent.focus(trigger);
    const tooltip = document.getElementById(
      trigger.getAttribute("aria-describedby") as string
    );
    expect(tooltip).toHaveAttribute("role", "tooltip");
    expect(tooltip).toHaveTextContent(
      "Date de la dernière évaluation automatique de la qualité du RAG."
    );
  });
});

describe("Ragas card — indicator tooltips (hover + focus)", () => {
  it("provides five accessible info triggers (four indicators + date)", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    expect(screen.getAllByRole("button")).toHaveLength(5);
  });

  it("reveals the Fidélité explanation with its technical name on focus", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    const trigger = screen.getByRole("button", { name: /fidélité/i });
    fireEvent.focus(trigger);
    const tooltip = document.getElementById(
      trigger.getAttribute("aria-describedby") as string
    );
    expect(tooltip).toHaveTextContent("Fidélité (faithfulness)");
    expect(tooltip).toHaveTextContent(
      "La réponse colle-t-elle aux sources récupérées, sans rien inventer ?"
    );
  });

  it("reveals the Couverture du contexte explanation on hover", () => {
    render(<RagasGauges quality={OK_QUALITY} />);
    const trigger = screen.getByRole("button", { name: /couverture du contexte/i });
    fireEvent.mouseEnter(trigger);
    const tooltip = document.getElementById(
      trigger.getAttribute("aria-describedby") as string
    );
    expect(tooltip).toHaveTextContent("Couverture du contexte (context recall)");
    expect(tooltip).toHaveTextContent(
      "A-t-on récupéré toutes les sources nécessaires pour répondre ?"
    );
  });
});

describe("Ragas card — no_data state (cold start)", () => {
  it("shows a distinct empty message and no gauges or legend", () => {
    render(<RagasGauges quality={{ kind: "no_data" }} />);
    expect(screen.queryByText("Fidélité")).not.toBeInTheDocument();
    expect(screen.queryByText("≥ 0.85 bon")).not.toBeInTheDocument();
    expect(screen.getByText(/pas encore de données|aucune/i)).toBeInTheDocument();
  });
});

describe("Ragas card — error state", () => {
  it("shows only the request id, no path/query/db detail", () => {
    render(
      <RagasGauges
        quality={{ kind: "error", request_id: "req-xyz-789" }}
      />
    );
    expect(screen.getByText(/req-xyz-789/)).toBeInTheDocument();
    expect(screen.queryByText("Fidélité")).not.toBeInTheDocument();
    expect(screen.queryByText("≥ 0.85 bon")).not.toBeInTheDocument();
  });

  it("keeps the card region labelled in every state", () => {
    const { rerender } = render(
      <RagasGauges quality={{ kind: "no_data" }} />
    );
    expect(screen.getByLabelText("Qualité RAG")).toBeInTheDocument();
    rerender(<RagasGauges quality={OK_QUALITY} />);
    const card = screen.getByLabelText("Qualité RAG");
    expect(within(card).getByText("Fidélité")).toBeInTheDocument();
  });
});
