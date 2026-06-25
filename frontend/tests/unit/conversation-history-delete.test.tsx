// Issue #287 — trash icon + delete affordances in the history list.
//
// Behaviour verified through the public render output (integration-style):
//   - every entry exposes a trash button labelled "Supprimer la conversation"
//   - clicking it fires onRequestDelete(id) (never onSelect)
//   - has_ticket === true → trash disabled + explanatory tooltip
//   - the row being deleted (deletingId) is greyed, shows a spinner, and is disabled
//
// Prior art: tests/unit/conversation-history.test.tsx.

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ConversationHistory } from "@/components/conversation-history/ConversationHistory";
import type { ConversationSummary } from "@/components/conversation-history/types";

vi.mock(
  "@/components/conversation-history/ConversationHistory.module.css",
  () => ({
    default: new Proxy({}, { get: (_t, prop: string) => prop }),
  })
);

function makeConversation(
  overrides: Partial<ConversationSummary> = {}
): ConversationSummary {
  return {
    id: "c1",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T10:30:00Z",
    message_count: 4,
    title: "Quelle est la capitale ?",
    has_ticket: false,
    ...overrides,
  };
}

const TRASH_LABEL = "Supprimer la conversation";

function renderHistory(
  conversations: ConversationSummary[],
  overrides: {
    onSelect?: (id: string) => void;
    onRequestDelete?: (id: string) => void;
    deletingId?: string | null;
  } = {}
) {
  const onSelect = overrides.onSelect ?? vi.fn();
  const onRequestDelete = overrides.onRequestDelete ?? vi.fn();
  render(
    <ConversationHistory
      conversations={conversations}
      selectedId={null}
      onSelect={onSelect}
      onRequestDelete={onRequestDelete}
      deletingId={overrides.deletingId ?? null}
    />
  );
  return { onSelect, onRequestDelete };
}

describe("ConversationHistory — trash affordance (#287)", () => {
  it("renders a trash button per entry", () => {
    renderHistory([
      makeConversation({ id: "a" }),
      makeConversation({ id: "b" }),
    ]);
    expect(screen.getAllByRole("button", { name: TRASH_LABEL })).toHaveLength(2);
  });

  it("fires onRequestDelete(id) when the trash button is clicked, not onSelect", () => {
    const { onSelect, onRequestDelete } = renderHistory([
      makeConversation({ id: "abc" }),
    ]);
    fireEvent.click(screen.getByRole("button", { name: TRASH_LABEL }));
    expect(onRequestDelete).toHaveBeenCalledWith("abc");
    expect(onSelect).not.toHaveBeenCalled();
  });
});

describe("ConversationHistory — proactive ticket block (#287)", () => {
  it("disables the trash button and exposes a tooltip when has_ticket is true", () => {
    renderHistory([makeConversation({ id: "t", has_ticket: true })]);
    const trash = screen.getByRole("button", { name: TRASH_LABEL });
    expect(trash).toBeDisabled();
    expect(trash).toHaveAttribute("title");
    expect(trash.getAttribute("title")).toMatch(/signalement/i);
  });

  it("keeps the trash button enabled when has_ticket is false", () => {
    renderHistory([makeConversation({ id: "ok", has_ticket: false })]);
    expect(screen.getByRole("button", { name: TRASH_LABEL })).toBeEnabled();
  });
});

describe("ConversationHistory — in-flight delete state (#287)", () => {
  it("greys, marks busy, and disables the row being deleted", () => {
    renderHistory([makeConversation({ id: "x" })], { deletingId: "x" });
    const item = screen.getByTestId("conversation-item-x");
    expect(item).toBeDisabled();
    expect(item).toHaveAttribute("aria-busy", "true");
    expect(screen.getByTestId("conversation-item-x")).toBeInTheDocument();
  });

  it("disables the trash button while its own row is being deleted", () => {
    renderHistory([makeConversation({ id: "x" })], { deletingId: "x" });
    expect(screen.getByRole("button", { name: TRASH_LABEL })).toBeDisabled();
  });
});
