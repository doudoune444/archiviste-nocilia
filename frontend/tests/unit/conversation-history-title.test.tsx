// AC #250 — history items render the derived title only (no date/count), as
// escaped text, and clicking an item still selects it (loads its transcript).

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
    updated_at: "2026-01-02T09:30:00Z",
    message_count: 4,
    title: "Quelle est l'histoire de Nocilia ?",
    ...overrides,
  };
}

describe("ConversationHistory — derived title rendering (#250)", () => {
  it("renders the title text on the history item", () => {
    render(
      <ConversationHistory
        conversations={[makeConversation()]}
        selectedId={null}
        onSelect={() => {}}
        onNew={() => {}}
      />
    );
    const item = screen.getByTestId("conversation-item-c1");
    expect(item).toHaveTextContent("Quelle est l'histoire de Nocilia ?");
  });

  it("shows the title only — neither the message count nor the date appears", () => {
    render(
      <ConversationHistory
        conversations={[makeConversation()]}
        selectedId={null}
        onSelect={() => {}}
        onNew={() => {}}
      />
    );
    const item = screen.getByTestId("conversation-item-c1");
    expect(item.textContent).not.toMatch(/message/i);
    expect(item.textContent).not.toMatch(/\d{2}\/\d{2}\/\d{2}/);
  });

  it("renders the title as escaped text, never as HTML", () => {
    render(
      <ConversationHistory
        conversations={[
          makeConversation({ title: "<img src=x onerror=alert(1)>" }),
        ]}
        selectedId={null}
        onSelect={() => {}}
        onNew={() => {}}
      />
    );
    const item = screen.getByTestId("conversation-item-c1");
    expect(item.querySelector("img")).toBeNull();
    expect(item).toHaveTextContent("<img src=x onerror=alert(1)>");
  });

  it("fires onSelect with the conversation id when clicked (loads transcript)", () => {
    const onSelect = vi.fn();
    render(
      <ConversationHistory
        conversations={[makeConversation()]}
        selectedId={null}
        onSelect={onSelect}
        onNew={() => {}}
      />
    );
    fireEvent.click(screen.getByTestId("conversation-item-c1"));
    expect(onSelect).toHaveBeenCalledWith("c1");
  });
});
