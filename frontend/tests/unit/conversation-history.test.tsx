// AC #250 — derived history titles, title-only rendering.
//
// AC: each history item shows the title only on one line (no date, no message count).
// AC: clicking a history item still fires onSelect(id) (transcript load unchanged).
// AC: the title is rendered as escaped text, never HTML.

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

describe("ConversationHistory — derived titles (#250)", () => {
  it("renders the title text for a history item", () => {
    render(
      <ConversationHistory
        conversations={[makeConversation()]}
        selectedId={null}
        onSelect={() => {}}
        onRequestDelete={() => {}}
        deletingId={null}
      />
    );
    expect(screen.getByText("Quelle est la capitale ?")).toBeInTheDocument();
  });

  it("shows the title only — no date and no message count", () => {
    render(
      <ConversationHistory
        conversations={[makeConversation()]}
        selectedId={null}
        onSelect={() => {}}
        onRequestDelete={() => {}}
        deletingId={null}
      />
    );
    expect(screen.queryByText(/message/)).not.toBeInTheDocument();
    expect(screen.queryByText(/\d{2}\/\d{2}\/\d{2}/)).not.toBeInTheDocument();
  });

  it("fires onSelect(id) when a history item is clicked", () => {
    const onSelect = vi.fn();
    render(
      <ConversationHistory
        conversations={[makeConversation({ id: "abc" })]}
        selectedId={null}
        onSelect={onSelect}
        onRequestDelete={() => {}}
        deletingId={null}
      />
    );
    fireEvent.click(screen.getByTestId("conversation-item-abc"));
    expect(onSelect).toHaveBeenCalledWith("abc");
  });

  it("renders a title as escaped text, never as HTML", () => {
    const malicious = '<img src=x onerror="alert(1)">';
    render(
      <ConversationHistory
        conversations={[makeConversation({ id: "x", title: malicious })]}
        selectedId={null}
        onSelect={() => {}}
        onRequestDelete={() => {}}
        deletingId={null}
      />
    );
    const item = screen.getByTestId("conversation-item-x");
    expect(item.querySelector("img")).toBeNull();
    expect(screen.getByText(malicious)).toBeInTheDocument();
  });
});
