// AC: CHAT-004 — conversation history sidebar (transcript→Message mapping)
//
// AC-transcript: clicking a conversation loads its full transcript IN ORDER.
// AC-order: messages rendered by ordinal ascending (ordinal is the sort key).
// AC-mapping: role + content map to Message { role, text }; unknown fields left undefined.
// AC-no-phantom: default page state is an empty thread (no auto-load on mount).

import { describe, it, expect } from "vitest";
import { mapTranscriptToMessages } from "@/components/conversation-history/transcript";
import type { ConversationMessage } from "@/components/conversation-history/types";

describe("mapTranscriptToMessages()", () => {
  // AC-mapping: a user row maps to { role: "user", text: content }
  it("maps a user row to a user message", () => {
    const rows: ConversationMessage[] = [
      { role: "user", ordinal: 0, content: "Qui est Nocilia ?" },
    ];
    const msgs = mapTranscriptToMessages(rows);
    expect(msgs).toHaveLength(1);
    expect(msgs[0]).toEqual({ role: "user", text: "Qui est Nocilia ?" });
  });

  // AC-mapping: an assistant row maps to { role: "assistant", text: content }
  it("maps an assistant row to an assistant message", () => {
    const rows: ConversationMessage[] = [
      { role: "assistant", ordinal: 1, content: "Nocilia est une archiviste." },
    ];
    const msgs = mapTranscriptToMessages(rows);
    expect(msgs).toHaveLength(1);
    expect(msgs[0]).toEqual({
      role: "assistant",
      text: "Nocilia est une archiviste.",
    });
  });

  // AC-order: messages must be in ordinal order (ascending)
  it("preserves ordinal order for a multi-turn transcript", () => {
    const rows: ConversationMessage[] = [
      { role: "assistant", ordinal: 1, content: "Bonjour !" },
      { role: "user", ordinal: 0, content: "Salut !" },
      { role: "assistant", ordinal: 3, content: "Au revoir !" },
      { role: "user", ordinal: 2, content: "Comment ça va ?" },
    ];
    const msgs = mapTranscriptToMessages(rows);
    expect(msgs.map((m) => m.text)).toEqual([
      "Salut !",
      "Bonjour !",
      "Comment ça va ?",
      "Au revoir !",
    ]);
  });

  // AC-mapping: extra optional fields (mode, citations, conversationId) are NOT set
  // when the transcript row does not provide them
  it("leaves optional fields undefined when not in the transcript row", () => {
    const rows: ConversationMessage[] = [
      { role: "user", ordinal: 0, content: "test" },
    ];
    const [msg] = mapTranscriptToMessages(rows);
    expect(msg).toBeDefined();
    expect("mode" in (msg ?? {})).toBe(false);
    expect("citations" in (msg ?? {})).toBe(false);
    expect("conversationId" in (msg ?? {})).toBe(false);
  });

  // AC-no-phantom: empty array stays empty (no phantom messages)
  it("returns an empty array for an empty transcript", () => {
    expect(mapTranscriptToMessages([])).toEqual([]);
  });
});
