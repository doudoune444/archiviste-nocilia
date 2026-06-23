// AC #245 — history items show a title only (the start of the first user
// message), no date, no message count.

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ConversationHistory } from "@/components/conversation-history/ConversationHistory";
import type { ConversationSummary } from "@/components/conversation-history/types";

vi.mock("@/components/conversation-history/ConversationHistory.module.css", () => ({
  default: new Proxy({}, { get: (_t, prop: string) => prop }),
}));

function makeConversation(
  overrides: Partial<ConversationSummary> = {}
): ConversationSummary {
  return {
    id: "c1",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
    message_count: 4,
    title: "Qui est Blowen ?",
    ...overrides,
  };
}

describe("ConversationHistory title display (#245)", () => {
  it("renders the conversation title", () => {
    render(
      <ConversationHistory
        conversations={[makeConversation()]}
        selectedId={null}
        onSelect={() => {}}
      />
    );
    expect(screen.getByText("Qui est Blowen ?")).toBeInTheDocument();
  });

  it("does not render the message count in the item", () => {
    render(
      <ConversationHistory
        conversations={[makeConversation({ message_count: 7 })]}
        selectedId={null}
        onSelect={() => {}}
      />
    );
    expect(screen.queryByText(/message/i)).not.toBeInTheDocument();
  });

  it("clicking an item fires onSelect with the conversation id", () => {
    const onSelect = vi.fn();
    render(
      <ConversationHistory
        conversations={[makeConversation({ id: "abc" })]}
        selectedId={null}
        onSelect={onSelect}
      />
    );
    screen.getByTestId("conversation-item-abc").click();
    expect(onSelect).toHaveBeenCalledWith("abc");
  });
});
