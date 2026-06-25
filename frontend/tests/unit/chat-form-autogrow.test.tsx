// AC #321 — textarea auto-grow in JS (scrollHeight, cap 200px, cross-browser).
//
// jsdom does not compute layout, so scrollHeight is 0. These tests assert the
// WIRING, not pixel values: after a value change, the textarea's inline height
// is set from scrollHeight and capped at MAX_HEIGHT. The effect must also tolerate
// a null ref (no crash) and reset to one line when the field is emptied.
//
// Tests assert observable behavior through the public component interface
// (rendered DOM inline style), never internal state.

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ChatForm } from "@/components/chat/ChatForm";

vi.mock("@/components/chat/chat.module.css", () => ({
  default: new Proxy({}, { get: (_t, prop: string) => prop }),
}));

/** Pins scrollHeight (jsdom returns 0) to drive the auto-grow math. */
function mockScrollHeight(element: HTMLElement, pixels: number): void {
  Object.defineProperty(element, "scrollHeight", {
    configurable: true,
    get: () => pixels,
  });
}

const MAX_HEIGHT_PX = 200;

describe("AutoGrowTextarea — JS auto-grow (#321)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("sets inline height from scrollHeight after a value change", () => {
    render(<ChatForm />);
    const textarea = screen.getByRole("textbox", {
      name: /votre question/i,
    }) as HTMLTextAreaElement;

    mockScrollHeight(textarea, 80);
    fireEvent.change(textarea, { target: { value: "Ligne un\nLigne deux" } });

    expect(textarea.style.height).toBe("80px");
  });

  it("caps the height at 200px when content overflows", () => {
    render(<ChatForm />);
    const textarea = screen.getByRole("textbox", {
      name: /votre question/i,
    }) as HTMLTextAreaElement;

    mockScrollHeight(textarea, 999);
    fireEvent.change(textarea, {
      target: { value: "a\nb\nc\nd\ne\nf\ng\nh\ni\nj\nk\nl" },
    });

    expect(textarea.style.height).toBe(`${MAX_HEIGHT_PX}px`);
  });

  it("recomputes a single line height after the field is emptied (reset on send)", () => {
    render(<ChatForm />);
    const textarea = screen.getByRole("textbox", {
      name: /votre question/i,
    }) as HTMLTextAreaElement;

    mockScrollHeight(textarea, 120);
    fireEvent.change(textarea, { target: { value: "plusieurs\nlignes" } });
    expect(textarea.style.height).toBe("120px");

    mockScrollHeight(textarea, 28);
    fireEvent.change(textarea, { target: { value: "" } });
    expect(textarea.style.height).toBe("28px");
  });
});
