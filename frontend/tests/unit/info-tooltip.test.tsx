// Unit tests for InfoTooltip — Observabilité Lot 1 slice 1 (issue #251).
//
// Behaviour verified through the public interface (rendered DOM + user events),
// never implementation details. Prior art: tests/unit/dep-health-poll.test.tsx.
//
// Acceptance criteria covered here:
//   - default-closed (tooltip content absent until opened)
//   - open-on-click
//   - open-on-keyboard-focus
//   - open-on-mouse-hover
//   - close-on-Escape
//   - close-on-outside-click
//   - trigger is a <button> with an accessible label (aria-label)
//   - aria-describedby linkage between trigger and tooltip content

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { InfoTooltip } from "@/components/info-tooltip/InfoTooltip";

afterEach(cleanup);

const LABEL = "À propos du score";
const CONTENT = "Score agrégé des évaluations RAGAS sur la période.";

function renderTooltip() {
  return render(<InfoTooltip label={LABEL} content={CONTENT} />);
}

describe("InfoTooltip", () => {
  it("renders the trigger as a button with an accessible label", () => {
    renderTooltip();
    const trigger = screen.getByRole("button", { name: LABEL });
    expect(trigger).toBeInTheDocument();
    expect(trigger.tagName).toBe("BUTTON");
  });

  it("is closed by default — content is not rendered", () => {
    renderTooltip();
    expect(screen.queryByText(CONTENT)).not.toBeInTheDocument();
  });

  it("opens on click", () => {
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
    fireEvent.click(screen.getByRole("button", { name: LABEL }));
    expect(screen.getByText(CONTENT)).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByText(CONTENT)).not.toBeInTheDocument();
  });

  it("closes on outside-click", () => {
    render(
      <div>
        <span data-testid="outside">outside</span>
        <InfoTooltip label={LABEL} content={CONTENT} />
      </div>
    );
    fireEvent.click(screen.getByRole("button", { name: LABEL }));
    expect(screen.getByText(CONTENT)).toBeInTheDocument();

    fireEvent.mouseDown(screen.getByTestId("outside"));
    expect(screen.queryByText(CONTENT)).not.toBeInTheDocument();
  });

  it("links the tooltip content to the trigger via aria-describedby", () => {
    renderTooltip();
    const trigger = screen.getByRole("button", { name: LABEL });
    fireEvent.click(trigger);

    const describedBy = trigger.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();

    const tooltip = screen.getByText(CONTENT);
    expect(tooltip).toHaveAttribute("id", describedBy);
  });
});
