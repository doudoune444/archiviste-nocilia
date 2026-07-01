// #375 (PRD #372) — re-hydrate the rich render on reload from history.
//
// On reload the transcript row carries only the raw persisted `content`
// (#374: answer body + inline `[source_path]` markers + `---SUIVI---` block).
// The live-stream metadata (citations, followups) is never stored, so the client
// reconstructs it from the string. parsePersistedAnswer mirrors the worker's
// extract_followups + extract_citations (parser.py) so a reloaded turn renders
// identically to the freshly-streamed one.

import { describe, it, expect } from "vitest";
import { parsePersistedAnswer } from "@/components/conversation-history/persisted-answer";

describe("parsePersistedAnswer — follow-up block split", () => {
  it("splits the body from the ---SUIVI--- block and lists the follow-ups", () => {
    const content = "Corps de réponse.\n---SUIVI---\n- Q1 ?\n- Q2 ?";
    const parsed = parsePersistedAnswer(content);
    expect(parsed.text).toBe("Corps de réponse.");
    expect(parsed.followups).toEqual(["Q1 ?", "Q2 ?"]);
  });

  it("returns the body verbatim and no follow-ups when the marker is absent", () => {
    const parsed = parsePersistedAnswer("Réponse simple sans suivi.");
    expect(parsed.text).toBe("Réponse simple sans suivi.");
    expect(parsed.followups).toEqual([]);
  });

  it("tolerates a marker variant and a dangling rule (mistral drift, #345)", () => {
    const content = "Corps.\n---\nSUIVI---\n* Q1 ?\n• Q2 ?";
    const parsed = parsePersistedAnswer(content);
    expect(parsed.text).toBe("Corps.");
    expect(parsed.followups).toEqual(["Q1 ?", "Q2 ?"]);
  });

  it("does not cut on the French word 'suivi' mid-sentence", () => {
    const content = "L'Archiviste assure le suivi des âmes de Nocilia.";
    const parsed = parsePersistedAnswer(content);
    expect(parsed.text).toBe(content);
    expect(parsed.followups).toEqual([]);
  });

  it("ignores blank lines inside the follow-up block", () => {
    const content = "Corps.\n---SUIVI---\n\n- Q1 ?\n\n- Q2 ?\n";
    const parsed = parsePersistedAnswer(content);
    expect(parsed.followups).toEqual(["Q1 ?", "Q2 ?"]);
  });
});

describe("parsePersistedAnswer — citations from inline markers", () => {
  it("derives one citation per distinct [source_path], in first-appearance order", () => {
    const content = "Fait un [lore/a.md] puis un autre [lore/b.md] et encore [lore/a.md].";
    const parsed = parsePersistedAnswer(content);
    expect(parsed.citations).toEqual([
      { source_path: "lore/a.md" },
      { source_path: "lore/b.md" },
    ]);
  });

  it("splits a comma-grouped bracket [a, b] into two citations (#345)", () => {
    const parsed = parsePersistedAnswer("Affirmation [lore/a.md, lore/b.md].");
    expect(parsed.citations).toEqual([
      { source_path: "lore/a.md" },
      { source_path: "lore/b.md" },
    ]);
  });

  it("returns no citations when the body carries no markers", () => {
    expect(parsePersistedAnswer("Aucune source ici.").citations).toEqual([]);
  });

  it("does not treat a marker inside the follow-up block as a citation", () => {
    const content = "Corps sans source.\n---SUIVI---\n- Parle de [lore/x.md] ?";
    expect(parsePersistedAnswer(content).citations).toEqual([]);
  });

  it("keeps a non-path bracket literal (light shape filter, #375)", () => {
    // AC: a bracket that is not a real document path must not become a citation.
    const content =
      "Attention [note], voir [lore/a.md] et [IGNORE PRIOR] et [42].";
    expect(parsePersistedAnswer(content).citations).toEqual([
      { source_path: "lore/a.md" },
    ]);
  });

  it("keeps only the path-shaped pieces of a mixed comma group", () => {
    const content = "Mixte [note, lore/a.md].";
    expect(parsePersistedAnswer(content).citations).toEqual([
      { source_path: "lore/a.md" },
    ]);
  });

  it("does not treat a scheme-like bracket as a document path (security)", () => {
    const content = "Danger [javascript:alert(1)] et [lore/a.md].";
    expect(parsePersistedAnswer(content).citations).toEqual([
      { source_path: "lore/a.md" },
    ]);
  });
});
