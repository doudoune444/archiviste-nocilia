// Unit tests for the pure Ragas score-band classifier — issue #348 (PRD #346).
//
// AC: the pure classification function is tested at the 0.85 and 0.70 boundaries
// (above / equal / below) → good / fair / weak, with no DOM. The band drives the
// gauge colour (green / amber / red) shown on the Qualité · Ragas card.
//
// Thresholds (verbatim legend on the card):
//   ≥ 0.85       → bon     (good,  green)
//   0.70–0.85    → correct (fair,  amber)
//   < 0.70       → faible  (weak,  red)

import { describe, it, expect } from "vitest";
import { classifyRagasScore, type RagasBand } from "@/lib/ragas-bands";

describe("classifyRagasScore — 0.85 boundary (bon)", () => {
  it("classifies a score above 0.85 as good", () => {
    expect(classifyRagasScore(0.91)).toBe<RagasBand>("good");
  });

  it("classifies a score exactly at 0.85 as good (inclusive)", () => {
    expect(classifyRagasScore(0.85)).toBe<RagasBand>("good");
  });

  it("classifies a score just below 0.85 as fair", () => {
    expect(classifyRagasScore(0.8499)).toBe<RagasBand>("fair");
  });
});

describe("classifyRagasScore — 0.70 boundary (correct)", () => {
  it("classifies a score in the 0.70–0.85 range as fair", () => {
    expect(classifyRagasScore(0.76)).toBe<RagasBand>("fair");
  });

  it("classifies a score exactly at 0.70 as fair (inclusive)", () => {
    expect(classifyRagasScore(0.7)).toBe<RagasBand>("fair");
  });

  it("classifies a score just below 0.70 as weak", () => {
    expect(classifyRagasScore(0.6999)).toBe<RagasBand>("weak");
  });
});

describe("classifyRagasScore — weak band", () => {
  it("classifies a low score as weak", () => {
    expect(classifyRagasScore(0.42)).toBe<RagasBand>("weak");
  });

  it("classifies the floor 0 as weak", () => {
    expect(classifyRagasScore(0)).toBe<RagasBand>("weak");
  });

  it("classifies the ceiling 1 as good", () => {
    expect(classifyRagasScore(1)).toBe<RagasBand>("good");
  });
});
