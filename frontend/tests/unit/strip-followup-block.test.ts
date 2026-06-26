// #345 BUG A — stripFollowupBlock tolerant marker matching + streaming partial trim.
//
// Mirrors the worker extract_followups (parser.py): mistral-small is unreliable
// about the exact `---SUIVI---` sentinel, so the client must hide tolerant
// variants from the displayed answer and never flash a partial marker mid-stream.

import { describe, it, expect } from "vitest";
import { stripFollowupBlock } from "@/components/chat/ChatForm";

describe("stripFollowupBlock — committed answer (full tolerant marker)", () => {
  it("cuts at the canonical ---SUIVI--- marker", () => {
    const text = "Corps de réponse.\n---SUIVI---\n- Q1 ?\n- Q2 ?";
    expect(stripFollowupBlock(text)).toBe("Corps de réponse.");
  });

  it("cuts at SUIVI--- (no leading dashes)", () => {
    expect(stripFollowupBlock("Corps.\nSUIVI---\n- Q1 ?")).toBe("Corps.");
  });

  it("cuts at --- SUIVI --- (spaces around the token)", () => {
    expect(stripFollowupBlock("Corps.\n--- SUIVI ---\n- Q1 ?")).toBe("Corps.");
  });

  it("strips a dangling --- rule placed just before the marker line", () => {
    const result = stripFollowupBlock("Corps.\n---\nSUIVI---\n- Q1 ?");
    expect(result).toBe("Corps.");
    expect(result).not.toContain("---");
  });

  it("returns the body unchanged when no marker is present", () => {
    expect(stripFollowupBlock("Réponse simple sans suivi.")).toBe(
      "Réponse simple sans suivi."
    );
  });

  it("does NOT cut on the French word 'suivi' mid-line", () => {
    const text = "L'Archiviste assure le suivi des âmes de Nocilia.";
    expect(stripFollowupBlock(text)).toBe(text);
  });
});

describe("stripFollowupBlock — streaming partial marker trim", () => {
  it("trims a trailing partial '\\n---' before the token arrives", () => {
    expect(stripFollowupBlock("Texte visible.\n---")).toBe("Texte visible.");
  });

  it("trims a trailing partial '\\n--- SUI'", () => {
    expect(stripFollowupBlock("Texte visible.\n--- SUI")).toBe("Texte visible.");
  });

  it("trims a trailing partial '\\nSUIV'", () => {
    expect(stripFollowupBlock("Texte visible.\nSUIV")).toBe("Texte visible.");
  });

  it("keeps regular content while streaming (no false trim)", () => {
    expect(stripFollowupBlock("Une phrase en cours d'écriture")).toBe(
      "Une phrase en cours d'écriture"
    );
  });
});
