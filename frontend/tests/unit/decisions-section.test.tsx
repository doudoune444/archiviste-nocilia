/**
 * Décisions & pistes d'amélioration — section render test (issue #351, PRD #346).
 *
 * Primary seam: the `DecisionsSection` server component rendered standalone. We
 * assert externally observable behaviour — the section title/subtitle, the five
 * decision cards (badge, title, monospace kicker, state paragraph with inline
 * `code`, and the "Piste d'amélioration" inset) and the verbatim prose — never
 * CSS classes or internal DOM structure.
 *
 * The editorial prose lives in a content module separate from layout; this test
 * asserts the prose surfaced by the layout, not the module's internal shape.
 */
import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import React from "react";

import { DecisionsSection } from "@/components/decisions/DecisionsSection";

describe("Décisions & pistes d'amélioration — section (#351)", () => {
  it("renders the section title and subtitle", () => {
    render(<DecisionsSection />);
    expect(
      screen.getByRole("heading", {
        level: 2,
        name: "Décisions & pistes d'amélioration",
      })
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Les décisions sont adaptées à mon besoin et à mes contraintes ; de meilleures solutions restent envisageables."
      )
    ).toBeInTheDocument();
  });

  it("renders exactly five decision cards", () => {
    render(<DecisionsSection />);
    expect(screen.getAllByRole("listitem")).toHaveLength(5);
  });

  it("numbers the badges 1 through 5 in order", () => {
    render(<DecisionsSection />);
    const cards = screen.getAllByRole("listitem");
    cards.forEach((card, index) => {
      expect(
        within(card).getByText(String(index + 1))
      ).toBeInTheDocument();
    });
  });

  it("renders the five decision titles verbatim", () => {
    render(<DecisionsSection />);
    const titles = [
      "Deux appels LLM en série",
      "Récupération — pgvector, top-5",
      "Représenter le sens des passages",
      "Découper les documents",
      "Mesurer la qualité des réponses",
    ];
    for (const title of titles) {
      expect(
        screen.getByRole("heading", { level: 3, name: title })
      ).toBeInTheDocument();
    }
  });

  it("renders the monospace technical kickers verbatim", () => {
    render(<DecisionsSection />);
    const kickers = [
      "classification + génération",
      "PostgreSQL + HNSW · cosinus",
      "mistral-embed · 1024 dimensions",
      "512 tokens · recouvrement 64",
      "4 métriques Ragas · 46 Q/R",
    ];
    for (const kicker of kickers) {
      expect(screen.getByText(kicker)).toBeInTheDocument();
    }
  });

  it("renders the first decision's state paragraph with inline code verbatim", () => {
    render(<DecisionsSection />);
    const firstCard = screen.getAllByRole("listitem")[0];
    expect(
      within(firstCard).getByText(/Chaque requête passe par deux appels au même modèle/)
    ).toBeInTheDocument();
    // Inline code segment rendered as a <code> element, not raw HTML.
    const code = within(firstCard).getByText("mistral-small-latest");
    expect(code.tagName).toBe("CODE");
  });

  it("gives every card a « Piste d'amélioration » inset", () => {
    render(<DecisionsSection />);
    const cards = screen.getAllByRole("listitem");
    expect(cards).toHaveLength(5);
    for (const card of cards) {
      expect(
        within(card).getByText("Piste d'amélioration")
      ).toBeInTheDocument();
    }
  });

  it("renders an improvement narrative verbatim", () => {
    render(<DecisionsSection />);
    expect(
      screen.getByText(
        /Aiguiller selon la difficulté : garder ce petit modèle pour les questions simples/
      )
    ).toBeInTheDocument();
  });

  it("renders the pgvector top-5 inline code in the second card's state", () => {
    render(<DecisionsSection />);
    const secondCard = screen.getAllByRole("listitem")[1];
    const code = within(secondCard).getByText("5");
    expect(code.tagName).toBe("CODE");
  });
});
