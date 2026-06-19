// AC BOARD-003: `category-filter` deep module round-trips selections ↔ search params correctly.
// Covers:
//   - category set / clear round-trip
//   - sort=priority | date round-trip
//   - unknown sort falls back to "priority" default
//   - buildGatewayParams produces the expected gateway query string
//   - buildPaginationParams preserves filter+sort with correct offset
import { describe, it, expect } from "vitest";
import {
  filterFromParams,
  filterToParams,
  buildGatewayParams,
  buildPaginationParams,
} from "@/components/category-filter/params";

describe("filterFromParams()", () => {
  // AC BOARD-003: category set
  it("reads category from URLSearchParams", () => {
    const p = new URLSearchParams("category=lore&sort=priority");
    const filter = filterFromParams(p);
    expect(filter.category).toBe("lore");
    expect(filter.sort).toBe("priority");
  });

  // AC BOARD-003: no category → undefined
  it("returns undefined category when param is absent", () => {
    const p = new URLSearchParams("sort=date");
    const filter = filterFromParams(p);
    expect(filter.category).toBeUndefined();
    expect(filter.sort).toBe("date");
  });

  // AC BOARD-003: empty category string → undefined
  it("treats empty category string as undefined", () => {
    const p = new URLSearchParams("category=");
    const filter = filterFromParams(p);
    expect(filter.category).toBeUndefined();
  });

  // AC BOARD-003: unknown sort falls back to priority default
  it("falls back to priority for unknown sort value", () => {
    const p = new URLSearchParams("sort=unknown");
    const filter = filterFromParams(p);
    expect(filter.sort).toBe("priority");
  });

  // AC BOARD-003: no params → defaults
  it("defaults to no category and sort=priority when params are empty", () => {
    const p = new URLSearchParams();
    const filter = filterFromParams(p);
    expect(filter.category).toBeUndefined();
    expect(filter.sort).toBe("priority");
  });

  // AC BOARD-003: sort=date
  it("parses sort=date correctly", () => {
    const p = new URLSearchParams("sort=date");
    const filter = filterFromParams(p);
    expect(filter.sort).toBe("date");
  });
});

describe("filterToParams() round-trip", () => {
  // AC BOARD-003: category set → round-trip back to UI state
  it("sets category in URLSearchParams", () => {
    const params = filterToParams({ category: "lore", sort: "priority" });
    expect(params.get("category")).toBe("lore");
    expect(params.has("sort")).toBe(false); // priority is the default, omitted
  });

  // AC BOARD-003: category clear → category param removed
  it("removes category param when category is undefined", () => {
    const existing = new URLSearchParams("category=lore&sort=date");
    const params = filterToParams({ category: undefined, sort: "date" }, existing);
    expect(params.has("category")).toBe(false);
    expect(params.get("sort")).toBe("date");
  });

  // AC BOARD-003: sort=date is written to params
  it("writes sort=date to URLSearchParams", () => {
    const params = filterToParams({ category: undefined, sort: "date" });
    expect(params.get("sort")).toBe("date");
  });

  // AC BOARD-003: sort=priority is omitted (default, clean URL)
  it("omits sort param when sort is the default (priority)", () => {
    const params = filterToParams({ category: undefined, sort: "priority" });
    expect(params.has("sort")).toBe(false);
  });

  // AC BOARD-003: full round-trip — write then read back
  it("round-trips category=lore + sort=date through write→read", () => {
    const original = { category: "lore", sort: "date" as const };
    const written = filterToParams(original);
    const roundTripped = filterFromParams(written);
    expect(roundTripped.category).toBe("lore");
    expect(roundTripped.sort).toBe("date");
  });

  // AC BOARD-003: full round-trip — no category + priority (defaults)
  it("round-trips no-filter + sort=priority through write→read", () => {
    const original = { category: undefined, sort: "priority" as const };
    const written = filterToParams(original);
    const roundTripped = filterFromParams(written);
    expect(roundTripped.category).toBeUndefined();
    expect(roundTripped.sort).toBe("priority");
  });

  // AC BOARD-003: existing params (e.g. limit/offset) are preserved
  it("preserves existing params not related to filter/sort", () => {
    const existing = new URLSearchParams("limit=20&offset=40");
    const params = filterToParams({ category: "lore", sort: "date" }, existing);
    expect(params.get("limit")).toBe("20");
    expect(params.get("offset")).toBe("40");
    expect(params.get("category")).toBe("lore");
    expect(params.get("sort")).toBe("date");
  });
});

describe("buildGatewayParams()", () => {
  // AC BOARD-003: filter+sort drive the gateway parameters
  it("includes sort, limit, offset=0 for a base fetch", () => {
    const qs = buildGatewayParams({ category: undefined, sort: "priority" }, 20);
    const p = new URLSearchParams(qs);
    expect(p.get("sort")).toBe("priority");
    expect(p.get("limit")).toBe("20");
    expect(p.get("offset")).toBe("0");
    expect(p.has("category")).toBe(false);
  });

  it("includes category when set", () => {
    const qs = buildGatewayParams({ category: "lore", sort: "date" }, 20);
    const p = new URLSearchParams(qs);
    expect(p.get("category")).toBe("lore");
    expect(p.get("sort")).toBe("date");
    expect(p.get("offset")).toBe("0");
  });
});

describe("buildPaginationParams()", () => {
  // AC BOARD-003: load-more preserves filter+sort with correct offset
  it("carries filter+sort+offset for pagination", () => {
    const qs = buildPaginationParams({ category: "lore", sort: "date" }, 20, 20);
    const p = new URLSearchParams(qs);
    expect(p.get("category")).toBe("lore");
    expect(p.get("sort")).toBe("date");
    expect(p.get("limit")).toBe("20");
    expect(p.get("offset")).toBe("20");
  });

  it("omits category when undefined", () => {
    const qs = buildPaginationParams({ category: undefined, sort: "priority" }, 20, 40);
    const p = new URLSearchParams(qs);
    expect(p.has("category")).toBe(false);
    expect(p.get("offset")).toBe("40");
  });
});
