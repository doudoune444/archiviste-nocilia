// AC: CHAT-004 — conversation history sidebar (transcript→Message mapping)
//
// AC-transcript: clicking a conversation loads its full transcript IN ORDER.
// AC-order: messages rendered by ordinal ascending (ordinal is the sort key).
// AC-mapping: role + content map to Message { role, text }; unknown fields left undefined.
// AC-no-phantom: default page state is an empty thread (no auto-load on mount).
//
// #375 (PRD #372): the assistant turn is re-hydrated from its persisted `content`
// so a reloaded turn renders like the freshly-streamed one — the ---SUIVI--- block
// becomes pills, inline [source_path] markers become citations, and the
// conversation id is attached so the per-answer signal form re-appears.

import { describe, it, expect } from "vitest";
import { mapTranscriptToMessages } from "@/components/conversation-history/transcript";
import type { ConversationMessage } from "@/components/conversation-history/types";

const CONVERSATION_ID = "conv-1234";

describe("mapTranscriptToMessages()", () => {
  // AC-mapping: a user row maps to { role: "user", text: content } — no id attached
  it("maps a user row to a user message without a conversation id", () => {
    const rows: ConversationMessage[] = [
      { role: "user", ordinal: 0, content: "Qui est Nocilia ?" },
    ];
    const msgs = mapTranscriptToMessages(rows, CONVERSATION_ID);
    expect(msgs).toHaveLength(1);
    expect(msgs[0]).toEqual({ role: "user", text: "Qui est Nocilia ?" });
  });

  // AC-mapping + #375: an assistant row carries the conversation id (for the signal form)
  it("maps a plain assistant row to an assistant message carrying the conversation id", () => {
    const rows: ConversationMessage[] = [
      { role: "assistant", ordinal: 1, content: "Nocilia est une archiviste." },
    ];
    const msgs = mapTranscriptToMessages(rows, CONVERSATION_ID);
    expect(msgs).toHaveLength(1);
    expect(msgs[0]).toEqual({
      role: "assistant",
      text: "Nocilia est une archiviste.",
      conversationId: CONVERSATION_ID,
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
    const msgs = mapTranscriptToMessages(rows, CONVERSATION_ID);
    expect(msgs.map((m) => m.text)).toEqual([
      "Salut !",
      "Bonjour !",
      "Comment ça va ?",
      "Au revoir !",
    ]);
  });

  // #375: an assistant row with a ---SUIVI--- block re-hydrates the pills and
  // hides the raw sentinel from the displayed body.
  it("re-hydrates follow-up pills from a persisted ---SUIVI--- block", () => {
    const rows: ConversationMessage[] = [
      {
        role: "assistant",
        ordinal: 1,
        content: "Le Cérafon est un artefact.\n---SUIVI---\n- Qui l'a forgé ?\n- Où est-il ?",
      },
    ];
    const [msg] = mapTranscriptToMessages(rows, CONVERSATION_ID);
    expect(msg?.text).toBe("Le Cérafon est un artefact.");
    expect(msg?.followups).toEqual(["Qui l'a forgé ?", "Où est-il ?"]);
    expect(msg?.text).not.toContain("SUIVI");
  });

  // #375: inline [source_path] markers re-hydrate the citations list.
  it("re-hydrates citations from persisted inline markers", () => {
    const rows: ConversationMessage[] = [
      {
        role: "assistant",
        ordinal: 1,
        content: "Blowen vécut à Periste [lore/blowen.md] puis partit [lore/periste.md].",
      },
    ];
    const [msg] = mapTranscriptToMessages(rows, CONVERSATION_ID);
    expect(msg?.citations).toEqual([
      { source_path: "lore/blowen.md" },
      { source_path: "lore/periste.md" },
    ]);
  });

  // #375: a plain assistant row must not invent empty followups/citations arrays.
  it("omits followups and citations when the persisted body has neither", () => {
    const rows: ConversationMessage[] = [
      { role: "assistant", ordinal: 1, content: "Réponse simple." },
    ];
    const [msg] = mapTranscriptToMessages(rows, CONVERSATION_ID);
    expect(msg).toBeDefined();
    expect("followups" in (msg ?? {})).toBe(false);
    expect("citations" in (msg ?? {})).toBe(false);
    expect("mode" in (msg ?? {})).toBe(false);
  });

  // AC-mapping: a user row never carries assistant-only optional fields
  it("leaves optional fields undefined on a user row", () => {
    const rows: ConversationMessage[] = [
      { role: "user", ordinal: 0, content: "test" },
    ];
    const [msg] = mapTranscriptToMessages(rows, CONVERSATION_ID);
    expect(msg).toBeDefined();
    expect("mode" in (msg ?? {})).toBe(false);
    expect("citations" in (msg ?? {})).toBe(false);
    expect("conversationId" in (msg ?? {})).toBe(false);
  });

  // AC-no-phantom: empty array stays empty (no phantom messages)
  it("returns an empty array for an empty transcript", () => {
    expect(mapTranscriptToMessages([], CONVERSATION_ID)).toEqual([]);
  });
});
