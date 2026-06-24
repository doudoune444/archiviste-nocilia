// Unit tests for ConfirmDialog — issue #285 (prefactor a11y, parent #282).
//
// Behaviour verified through the public render output (integration-style),
// never internal state. Prior art: tests/unit/info-tooltip.test.tsx.
//
// Acceptance criteria covered:
//   - renders with role="dialog", aria-modal="true", title/message/danger-label
//   - initial focus lands on the Annuler (neutral cancel) button
//   - focus is trapped inside the dialog while open
//   - Esc, overlay click, and Annuler all invoke the cancel callback
//   - the danger button invokes the confirm callback and is styled as danger
//   - inline trash SVG icon component shipped, matching SendIcon conventions

import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { ConfirmDialog } from "@/components/confirm-dialog/ConfirmDialog";
import { TrashIcon } from "@/components/confirm-dialog/TrashIcon";

// jsdom cannot process real CSS modules; stub to identity proxy.
vi.mock("@/components/confirm-dialog/ConfirmDialog.module.css", () => ({
  default: new Proxy({}, { get: (_t, prop: string) => prop }),
}));

const TITLE = "Supprimer la conversation";
const MESSAGE = "Cette action est irréversible.";
const DANGER_LABEL = "Supprimer";

afterEach(() => {
  cleanup();
});

function renderDialog(overrides?: {
  onConfirm?: () => void;
  onCancel?: () => void;
}) {
  const onConfirm = overrides?.onConfirm ?? vi.fn();
  const onCancel = overrides?.onCancel ?? vi.fn();
  render(
    <ConfirmDialog
      title={TITLE}
      message={MESSAGE}
      dangerLabel={DANGER_LABEL}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
  return { onConfirm, onCancel };
}

describe("ConfirmDialog — structure and accessibility", () => {
  it("renders as a modal dialog carrying the title and message", () => {
    renderDialog();
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveTextContent(TITLE);
    expect(dialog).toHaveTextContent(MESSAGE);
  });
});

describe("ConfirmDialog — cancel paths", () => {
  it("invokes the cancel callback from the Annuler button", () => {
    const { onConfirm, onCancel } = renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "Annuler" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("invokes the cancel callback on Escape", () => {
    const { onConfirm, onCancel } = renderDialog();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("invokes the cancel callback on overlay click", () => {
    const { onCancel } = renderDialog();
    const dialog = screen.getByRole("dialog");
    const overlay = dialog.parentElement as HTMLElement;
    fireEvent.click(overlay);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("does not cancel when the click originates inside the dialog", () => {
    const { onCancel } = renderDialog();
    fireEvent.click(screen.getByRole("dialog"));
    expect(onCancel).not.toHaveBeenCalled();
  });
});

describe("ConfirmDialog — confirm path", () => {
  it("invokes the confirm callback from the danger button", () => {
    const { onConfirm, onCancel } = renderDialog();
    fireEvent.click(screen.getByRole("button", { name: DANGER_LABEL }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("styles the danger button distinctly from the cancel button", () => {
    renderDialog();
    const dangerButton = screen.getByRole("button", { name: DANGER_LABEL });
    const cancelButton = screen.getByRole("button", { name: "Annuler" });
    expect(dangerButton.className).toContain("danger");
    expect(cancelButton.className).not.toContain("danger");
  });
});

describe("ConfirmDialog — focus management", () => {
  it("places initial focus on the neutral Annuler button", () => {
    renderDialog();
    expect(screen.getByRole("button", { name: "Annuler" })).toHaveFocus();
  });

  it("traps focus inside the dialog when tabbing past the last element", () => {
    renderDialog();
    const cancelButton = screen.getByRole("button", { name: "Annuler" });
    const dangerButton = screen.getByRole("button", { name: DANGER_LABEL });

    dangerButton.focus();
    fireEvent.keyDown(dangerButton, { key: "Tab" });
    expect(cancelButton).toHaveFocus();
  });

  it("traps focus inside the dialog when shift-tabbing past the first element", () => {
    renderDialog();
    const cancelButton = screen.getByRole("button", { name: "Annuler" });
    const dangerButton = screen.getByRole("button", { name: DANGER_LABEL });

    cancelButton.focus();
    fireEvent.keyDown(cancelButton, { key: "Tab", shiftKey: true });
    expect(dangerButton).toHaveFocus();
  });
});

describe("TrashIcon — inline SVG matching SendIcon conventions", () => {
  it("renders a decorative SVG with currentColor stroke and no own label", () => {
    const { container } = render(
      <button type="button" aria-label="Supprimer la conversation">
        <TrashIcon />
      </button>
    );
    const svg = container.querySelector("svg") as SVGElement;
    expect(svg).not.toBeNull();
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(svg).toHaveAttribute("focusable", "false");
    expect(svg).toHaveAttribute("stroke", "currentColor");
    expect(screen.getByRole("button", { name: "Supprimer la conversation" })).toBeInTheDocument();
  });
});
