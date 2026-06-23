// Unit tests for InfoTooltip — issue #251 (Observabilité, Lot 1, slice 1).
//
// A reusable, dependency-free information-tooltip leaf component. The trigger
// is a focusable <button> carrying an info icon, with an aria-label and an
// aria-describedby pointing at the tooltip content when open.
//
// Coverage (per the issue's Testing Decisions):
//   - default-closed: tooltip content is not rendered initially
//   - open-on-click: clicking the trigger reveals the content
//   - open-on-focus: focusing the trigger reveals the content
//   - open-on-hover: hovering the trigger reveals the content
//   - close-on-Escape: pressing Escape hides the content
//   - close-on-outside-click: clicking outside hides the content
//   - the trigger button exposes an accessible label (aria-label)
//   - aria-describedby links the trigger to the content element's id
//
// Prior art: tests/unit/dep-health-poll.test.tsx.

import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { InfoTooltip } from "@/components/info-tooltip/InfoTooltip";

const LABEL = "Qu'est-ce que la fidélité ?";
const CONTENT = "La fidélité mesure l'ancrage de la réponse dans les sources.";

function renderTooltip() {
  return render(<InfoTooltip label={LABEL} content={CONTENT} />);
}

describe("InfoTooltip", () => {
  it("renders the trigger as a button with an accessible label", () => {
    renderTooltip();
    const trigger = screen.getByRole("button", { name: LABEL });
    expect(trigger).toBeInTheDocument();
  });

  it("is closed by default (content not shown)", () => {
    renderTooltip();
    expect(screen.queryByText(CONTENT)).not.toBeInTheDocument();
  });

  it("opens on click/tap", () => {
    renderTooltip();
    fireEvent.click(screen.getByRole("button", { name: LABEL }));
    expect(screen.getByText(CONTENT)).toBeInTheDocument();
  });

  it("opens on keyboard focus", () => {
    renderTooltip();
    fireEvent.focus(screen.getByRole("button", { name: LABEL }));
    expect(screen.getByText(CONTENT)).toBeInTheDocument();
  });

  it("opens on mouse hover", () => {
    renderTooltip();
    fireEvent.mouseEnter(screen.getByRole("button", { name: LABEL }));
    expect(screen.getByText(CONTENT)).toBeInTheDocument();
  });

  it("closes on Escape key", () => {
    renderTooltip();
    const trigger = screen.getByRole("button", { name: LABEL });
    fireEvent.click(trigger);
    expect(screen.getByText(CONTENT)).toBeInTheDocument();

    fireEvent.keyDown(trigger, { key: "Escape" });
    expect(screen.queryByText(CONTENT)).not.toBeInTheDocument();
  });

  it("closes on outside-click", () => {
    render(
      <div>
        <span data-testid="outside">elsewhere</span>
        <InfoTooltip label={LABEL} content={CONTENT} />
      </div>
    );
    fireEvent.click(screen.getByRole("button", { name: LABEL }));
    expect(screen.getByText(CONTENT)).toBeInTheDocument();

    fireEvent.mouseDown(screen.getByTestId("outside"));
    expect(screen.queryByText(CONTENT)).not.toBeInTheDocument();
  });

  it("links the trigger to the content via aria-describedby when open", () => {
    renderTooltip();
    const trigger = screen.getByRole("button", { name: LABEL });

    expect(trigger).not.toHaveAttribute("aria-describedby");

    fireEvent.click(trigger);

    const describedBy = trigger.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();

    const content = screen.getByText(CONTENT);
    expect(content).toHaveAttribute("id", describedBy as string);
  });
});
