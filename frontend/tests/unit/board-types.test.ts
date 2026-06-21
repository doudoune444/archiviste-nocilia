// #231: isBoardPage shape guard — categories field validation.
//
// AC: isBoardPage accepts a body with categories: [], rejects a body missing categories.
// AC: existing valid BoardPage shape with all required fields is accepted.

import { describe, it, expect } from "vitest";
import { isBoardPage } from "@/components/board/types";

describe("isBoardPage (#231)", () => {
  // AC: full valid shape is accepted.
  it("accepts a body with all required fields including categories", () => {
    const body = {
      items: [],
      total: 0,
      limit: 20,
      offset: 0,
      categories: [],
    };
    expect(isBoardPage(body)).toBe(true);
  });

  // AC: categories with values is accepted.
  it("accepts a body where categories is a non-empty array", () => {
    const body = {
      items: [],
      total: 3,
      limit: 20,
      offset: 0,
      categories: ["lore", "chronologie"],
    };
    expect(isBoardPage(body)).toBe(true);
  });

  // AC: missing categories field is rejected.
  it("rejects a body missing the categories field", () => {
    const body = {
      items: [],
      total: 0,
      limit: 20,
      offset: 0,
    };
    expect(isBoardPage(body)).toBe(false);
  });

  // AC: categories as non-array is rejected.
  it("rejects a body where categories is not an array", () => {
    const body = {
      items: [],
      total: 0,
      limit: 20,
      offset: 0,
      categories: "lore",
    };
    expect(isBoardPage(body)).toBe(false);
  });

  // AC: null is rejected.
  it("rejects null", () => {
    expect(isBoardPage(null)).toBe(false);
  });

  // AC: missing items is rejected.
  it("rejects a body missing the items field", () => {
    const body = {
      total: 0,
      limit: 20,
      offset: 0,
      categories: [],
    };
    expect(isBoardPage(body)).toBe(false);
  });

  // AC: missing total is rejected.
  it("rejects a body missing the total field", () => {
    const body = {
      items: [],
      limit: 20,
      offset: 0,
      categories: [],
    };
    expect(isBoardPage(body)).toBe(false);
  });
});
