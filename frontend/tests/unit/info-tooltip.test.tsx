// Unit tests for InfoTooltip — issue #246 (Qualité RAG lisible, Lot 1).
//
// Seam 2: the reusable info-tooltip leaf client. We assert observable
// behaviour, not internals:
//   - closed by default (explanation not exposed);
//   - the trigger is a <button> with an accessible label;
//   - opening on click reveals the explanation, linked via aria-describedby;
//   - Escape closes it;
//   - click outside closes it.
//
// Prior art: tests/unit/dep-health-poll.test.tsx (client component + interaction).

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { InfoTooltip } from "@/components/info-tooltip/InfoTooltip";

const LABEL = "En savoir plus sur Fidélité";
const EXPLANATION = "La réponse colle-t-elle aux sources récupérées, sans rien inventer ?";

afterEach(cleanup);

describe("InfoTooltip", () => {
  it("renders a button carrying the accessible label", () => {
    render(<InfoTooltip label={LABEL} explanation={EXPLANATION} />);
    const button = screen.getByRole("button", { name: LABEL });
    expect(button).toBeInTheDocument();
  });

  it("hides the explanation until activated", () => {
    render(<InfoTooltip label={LABEL} explanation={EXPLANATION} />);
    expect(screen.queryByText(EXPLANATION)).not.toBeInTheDocument();
  });

  it("reveals the explanation on click", () => {
    render(<InfoTooltip label={LABEL} explanation={EXPLANATION} />);
    fireEvent.click(screen.getByRole("button", { name: LABEL }));
    expect(screen.getByText(EXPLANATION)).toBeInTheDocument();
  });

  it("links the explanation to the button via aria-describedby", () => {
    render(<InfoTooltip label={LABEL} explanation={EXPLANATION} />);
    fireEvent.click(screen.getByRole("button", { name: LABEL }));

    const button = screen.getByRole("button", { name: LABEL });
    const describedBy = button.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();

    const explanationNode = screen.getByText(EXPLANATION);
    expect(explanationNode.id).toBe(describedBy);
  });

  it("reveals the explanation on mouse hover (desktop)", () => {
    render(<InfoTooltip label={LABEL} explanation={EXPLANATION} />);
    fireEvent.mouseEnter(screen.getByRole("button", { name: LABEL }));
    expect(screen.getByText(EXPLANATION)).toBeInTheDocument();
  });

  it("reveals the explanation on keyboard focus", () => {
    render(<InfoTooltip label={LABEL} explanation={EXPLANATION} />);
    fireEvent.focus(screen.getByRole("button", { name: LABEL }));
    expect(screen.getByText(EXPLANATION)).toBeInTheDocument();
  });

  it("closes on Escape key", () => {
    render(<InfoTooltip label={LABEL} explanation={EXPLANATION} />);
    fireEvent.click(screen.getByRole("button", { name: LABEL }));
    expect(screen.getByText(EXPLANATION)).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByText(EXPLANATION)).not.toBeInTheDocument();
  });

  it("closes on click outside", () => {
    render(
      <div>
        <span data-testid="outside">ailleurs</span>
        <InfoTooltip label={LABEL} explanation={EXPLANATION} />
      </div>
    );
    fireEvent.click(screen.getByRole("button", { name: LABEL }));
    expect(screen.getByText(EXPLANATION)).toBeInTheDocument();

    fireEvent.mouseDown(screen.getByTestId("outside"));
    expect(screen.queryByText(EXPLANATION)).not.toBeInTheDocument();
  });
});
