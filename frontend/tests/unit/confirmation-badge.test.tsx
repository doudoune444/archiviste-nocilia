// BOARD-002 AC2 — FIX-BADGE truthfulness invariant truth-table.
// judges_not_passed === true  → renders "non confirmé par les juges"
// ALL other cases (false, undefined, legacy null) → neutral, NO affirmative claim.
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ConfirmationBadge } from "@/components/board/ConfirmationBadge";

// Matches "confirmé" only when NOT immediately preceded by "non " — excludes the
// legitimate negative label "non confirmé par les juges" so the assertion
// genuinely proves no affirmative claim is present.
const AFFIRMATIVE_ONLY_PATTERN = /(?<!non\s)confirmé/i;

describe("ConfirmationBadge — FIX-BADGE invariant (AC2)", () => {
  // AC2: judges_not_passed true → must show the exact label
  it("renders 'non confirmé par les juges' when judges_not_passed is true", () => {
    render(<ConfirmationBadge judges_not_passed={true} />);
    expect(
      screen.getByText("non confirmé par les juges")
    ).toBeInTheDocument();
  });

  // AC2: judges_not_passed true → badge element present
  it("renders the badge element when judges_not_passed is true", () => {
    render(<ConfirmationBadge judges_not_passed={true} />);
    expect(screen.getByTestId("badge-not-confirmed")).toBeInTheDocument();
  });

  // AC2: judges_not_passed false → renders nothing; NO affirmative "confirmé" claim
  it("renders nothing when judges_not_passed is false", () => {
    const { container } = render(<ConfirmationBadge judges_not_passed={false} />);
    expect(container).toBeEmptyDOMElement();
  });

  // AC2: judges_not_passed undefined (legacy tickets) → renders nothing
  it("renders nothing when judges_not_passed is undefined", () => {
    const { container } = render(<ConfirmationBadge judges_not_passed={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  // AC2: guard — the false case must NEVER contain any affirmative "confirmé" wording
  // (pattern excludes "non confirmé" so an empty-renders-nothing component also passes)
  it("never shows an affirmative 'confirmé' claim when judges_not_passed is false", () => {
    render(<ConfirmationBadge judges_not_passed={false} />);
    expect(document.body.textContent).not.toMatch(AFFIRMATIVE_ONLY_PATTERN);
  });

  // AC2: guard — the undefined case must NEVER contain any affirmative "confirmé" wording
  it("never shows an affirmative 'confirmé' claim when judges_not_passed is undefined", () => {
    render(<ConfirmationBadge judges_not_passed={undefined} />);
    expect(document.body.textContent).not.toMatch(AFFIRMATIVE_ONLY_PATTERN);
  });
});
