// Issue #325 — active item left-rail + accent state (reconciles #287/#314).
//
// The aesthetic itself (hover/:focus, left rail color) is CSS-only and not
// unit-testable. The testable contract per the issue is the active-state
// CLASS: the item with aria-current="true" carries the `active` modifier
// class so the violet left rail / accent-ink text / font-weight rule can hook
// onto it, while non-selected items do not. Prop contract (onRequestDelete,
// deletingId, has_ticket) must keep working.
//
// The CSS module is mocked with a Proxy returning the prop name as the class,
// so a className contains the literal "active" iff styles.active was applied.
//
// Prior art: tests/unit/conversation-history.test.tsx, conversation-history-delete.test.tsx.

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
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

describe("ConversationHistory — active item state (#325)", () => {
  it("marks the selected item with aria-current=\"true\"", () => {
    render(
      <ConversationHistory
        conversations={[makeConversation({ id: "sel" })]}
        selectedId="sel"
        onSelect={() => {}}
        onRequestDelete={() => {}}
        deletingId={null}
      />
    );
    expect(screen.getByTestId("conversation-item-sel")).toHaveAttribute(
      "aria-current",
      "true"
    );
  });

  it("applies the active modifier class to the selected item only", () => {
    render(
      <ConversationHistory
        conversations={[
          makeConversation({ id: "sel" }),
          makeConversation({ id: "other" }),
        ]}
        selectedId="sel"
        onSelect={() => {}}
        onRequestDelete={() => {}}
        deletingId={null}
      />
    );
    expect(screen.getByTestId("conversation-item-sel").className).toContain(
      "active"
    );
    expect(
      screen.getByTestId("conversation-item-other").className
    ).not.toContain("active");
  });

  it("applies no active class when nothing is selected", () => {
    render(
      <ConversationHistory
        conversations={[makeConversation({ id: "none" })]}
        selectedId={null}
        onSelect={() => {}}
        onRequestDelete={() => {}}
        deletingId={null}
      />
    );
    const item = screen.getByTestId("conversation-item-none");
    expect(item).not.toHaveAttribute("aria-current");
    expect(item.className).not.toContain("active");
  });
});
