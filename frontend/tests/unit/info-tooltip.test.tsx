// Unit tests for InfoTooltip — issue #251 (Observabilité, Lot 1, slice 1)
//
// Behaviour verified through the public render output (integration-style),
// never internal state. Prior art: tests/unit/dep-health-poll.test.tsx,
// tests/unit/transcript-drawer.test.tsx.
//
// Acceptance criteria covered:
//   - renders an info trigger as a <button> with an accessible label (aria-label)
//   - tooltip content is closed by default
//   - opens on click/tap, on mouse hover, and on keyboard focus
//   - closes on Escape key and on outside-click
//   - tooltip content is linked to the trigger via aria-describedby

import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { InfoTooltip } from "@/components/info-tooltip/InfoTooltip";

// jsdom cannot process real CSS modules; stub to identity proxy.
vi.mock("@/components/info-tooltip/InfoTooltip.module.css", () => ({
  default: new Proxy({}, { get: (_t, prop: string) => prop }),
}));

const LABEL = "En savoir plus sur la fidélité";
const CONTENT = "La fidélité mesure si la réponse colle aux sources.";

afterEach(() => {
  cleanup();
});

function renderTooltip() {
  return render(<InfoTooltip label={LABEL} content={CONTENT} />);
}

describe("InfoTooltip — trigger and accessibility", () => {
  it("renders the trigger as a <button> carrying an accessible label", () => {
    renderTooltip();
    const trigger = screen.getByRole("button", { name: LABEL });
    expect(trigger).toBeInTheDocument();
    expect(trigger.tagName).toBe("BUTTON");
    expect(trigger).toHaveAttribute("aria-label", LABEL);
  });

  it("hides the tooltip content by default", () => {
    renderTooltip();
    expect(screen.queryByText(CONTENT)).not.toBeInTheDocument();
  });
});

describe("InfoTooltip — opening", () => {
  it("opens on click/tap", () => {
    renderTooltip();
    fireEvent.click(screen.getByRole("button", { name: LABEL }));
    expect(screen.getByText(CONTENT)).toBeInTheDocument();
  });

  it("opens on mouse hover", () => {
    renderTooltip();
    fireEvent.mouseEnter(screen.getByRole("button", { name: LABEL }));
    expect(screen.getByText(CONTENT)).toBeInTheDocument();
  });

  it("opens on keyboard focus", () => {
    renderTooltip();
    fireEvent.focus(screen.getByRole("button", { name: LABEL }));
    expect(screen.getByText(CONTENT)).toBeInTheDocument();
  });
});

describe("InfoTooltip — closing", () => {
  it("closes on Escape key", () => {
    renderTooltip();
    const trigger = screen.getByRole("button", { name: LABEL });
    fireEvent.click(trigger);
    expect(screen.getByText(CONTENT)).toBeInTheDocument();

    fireEvent.keyDown(trigger, { key: "Escape" });
    expect(screen.queryByText(CONTENT)).not.toBeInTheDocument();
  });

  it("closes on outside-click", () => {
    renderTooltip();
    fireEvent.click(screen.getByRole("button", { name: LABEL }));
    expect(screen.getByText(CONTENT)).toBeInTheDocument();

    fireEvent.pointerDown(document.body);
    expect(screen.queryByText(CONTENT)).not.toBeInTheDocument();
  });
});

describe("InfoTooltip — aria-describedby linkage", () => {
  it("links the visible tooltip content to the trigger via aria-describedby", () => {
    renderTooltip();
    const trigger = screen.getByRole("button", { name: LABEL });
    fireEvent.click(trigger);

    const describedBy = trigger.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();

    const tooltip = document.getElementById(describedBy as string);
    expect(tooltip).not.toBeNull();
    expect(tooltip).toHaveTextContent(CONTENT);
  });
});
