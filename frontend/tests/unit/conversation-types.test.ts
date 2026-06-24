// #284 — has_ticket on ConversationSummary, surfaced through isConversationList.
//
// The gateway computes has_ticket per item (EXISTS over tickets). The sidebar uses
// it to proactively disable the delete icon, so the parsing guard must require a
// boolean has_ticket on every item and reject a payload that omits it.

import { describe, it, expect } from "vitest";
import { isConversationList } from "@/components/conversation-history/types";

function makeItem(overrides: Record<string, unknown> = {}) {
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

describe("isConversationList — has_ticket (#284)", () => {
  it("accepts a list whose items carry has_ticket: false", () => {
    const body = { conversations: [makeItem({ has_ticket: false })] };
    expect(isConversationList(body)).toBe(true);
  });

  it("accepts a list whose items carry has_ticket: true", () => {
    const body = { conversations: [makeItem({ has_ticket: true })] };
    expect(isConversationList(body)).toBe(true);
  });

  it("accepts an empty conversations array", () => {
    expect(isConversationList({ conversations: [] })).toBe(true);
  });

  it("rejects an item missing the has_ticket field", () => {
    const item = makeItem();
    delete (item as Record<string, unknown>)["has_ticket"];
    expect(isConversationList({ conversations: [item] })).toBe(false);
  });

  it("rejects an item whose has_ticket is not a boolean", () => {
    const body = { conversations: [makeItem({ has_ticket: "yes" })] };
    expect(isConversationList(body)).toBe(false);
  });

  it("rejects a body without conversations", () => {
    expect(isConversationList({})).toBe(false);
  });

  it("rejects null", () => {
    expect(isConversationList(null)).toBe(false);
  });
});
