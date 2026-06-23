// Fetch-layer tests for WEBOBS-001 — fetchStats / fetchQuality boundary validation
//
// AC5: a malformed-200 body, a thrown rejection, or a non-200 must each produce
// kind:"error" and must never throw an unhandled exception. Mocks at the
// `forward` boundary so no real HTTP calls are made.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// next/headers is a Next.js server-only API unavailable in the test environment.
vi.mock("next/headers", () => ({
  headers: vi.fn().mockResolvedValue(new Headers()),
}));

// --- module mock for bff-proxy ---
vi.mock("@/lib/bff-proxy", () => ({
  forward: vi.fn(),
}));

import { fetchStats, fetchQuality } from "@/app/metriques/fetch";

// We need to import the mock handle so tests can configure it.
import { forward } from "@/lib/bff-proxy";
const mockForward = vi.mocked(forward);

const TEST_RID = "req-test-001";

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json",
      "x-request-id": TEST_RID,
    },
  });
}

beforeEach(() => {
  vi.resetAllMocks();
});

afterEach(() => {
  vi.resetAllMocks();
});

// ────────────────────────────────────────────────────────────────────────────
// fetchStats
// ────────────────────────────────────────────────────────────────────────────

describe("fetchStats", () => {
  // AC5: malformed 200 — missing conversation_count → kind:"error", never throws
  it("returns kind:error for a 200 body missing conversation_count", async () => {
    mockForward.mockResolvedValue(makeJsonResponse({}));
    const result = await fetchStats("rid-stats-1");
    expect(result.kind).toBe("error");
  });

  // AC5: malformed 200 — non-finite value → kind:"error"
  it("returns kind:error for a 200 body with non-finite conversation_count", async () => {
    mockForward.mockResolvedValue(makeJsonResponse({ conversation_count: null }));
    const result = await fetchStats("rid-stats-2");
    expect(result.kind).toBe("error");
  });

  // AC5: thrown rejection (e.g. network failure) → kind:"error", never rethrows
  it("returns kind:error when forward rejects (network failure)", async () => {
    mockForward.mockRejectedValue(new Error("network down"));
    const result = await fetchStats("rid-stats-3");
    expect(result.kind).toBe("error");
  });

  // AC5: non-200 response → kind:"error"
  it("returns kind:error for a non-200 response", async () => {
    mockForward.mockResolvedValue(makeJsonResponse({ error: "upstream_unavailable" }, 503));
    const result = await fetchStats("rid-stats-4");
    expect(result.kind).toBe("error");
  });

  // Regression: valid body still returns kind:ok
  it("returns kind:ok for a valid stats body", async () => {
    mockForward.mockResolvedValue(makeJsonResponse({ conversation_count: 7 }));
    const result = await fetchStats("rid-stats-5");
    expect(result.kind).toBe("ok");
    if (result.kind === "ok") {
      expect(result.conversation_count).toBe(7);
    }
  });
});

// ────────────────────────────────────────────────────────────────────────────
// fetchQuality
// ────────────────────────────────────────────────────────────────────────────

describe("fetchQuality", () => {
  // AC5 BLOCKER: malformed 200 (empty object) → kind:"error", never throws
  it("returns kind:error for a 200 body that is {} (shape drift)", async () => {
    mockForward.mockResolvedValue(makeJsonResponse({}));
    const result = await fetchQuality("rid-qual-1");
    expect(result.kind).toBe("error");
  });

  // AC5: malformed 200 — metrics present but golden_set_version missing → kind:"error"
  it("returns kind:error when scores are present but golden_set_version is missing", async () => {
    mockForward.mockResolvedValue(
      makeJsonResponse({
        faithfulness: 0.8,
        answer_relevancy: 0.9,
        context_precision: 0.7,
        context_recall: 0.85,
        finished_at: "2025-01-01T00:00:00Z",
        // golden_set_version intentionally absent
      })
    );
    const result = await fetchQuality("rid-qual-2");
    expect(result.kind).toBe("error");
  });

  // AC5: malformed 200 — one score is undefined → kind:"error"
  it("returns kind:error when one score field is absent", async () => {
    mockForward.mockResolvedValue(
      makeJsonResponse({
        faithfulness: 0.8,
        // answer_relevancy absent
        context_precision: 0.7,
        context_recall: 0.85,
        golden_set_version: "v1",
        finished_at: "2025-01-01T00:00:00Z",
      })
    );
    const result = await fetchQuality("rid-qual-3");
    expect(result.kind).toBe("error");
  });

  // AC5: thrown rejection → kind:"error", never rethrows
  it("returns kind:error when forward rejects (network failure)", async () => {
    mockForward.mockRejectedValue(new Error("network down"));
    const result = await fetchQuality("rid-qual-4");
    expect(result.kind).toBe("error");
  });

  // AC5: non-200 response → kind:"error"
  it("returns kind:error for a non-200 response", async () => {
    mockForward.mockResolvedValue(makeJsonResponse({ error: "upstream_unavailable" }, 503));
    const result = await fetchQuality("rid-qual-5");
    expect(result.kind).toBe("error");
  });

  // Regression: no_data response still returns kind:no_data
  it("returns kind:no_data for a 200 status:no_data body", async () => {
    mockForward.mockResolvedValue(makeJsonResponse({ status: "no_data" }));
    const result = await fetchQuality("rid-qual-6");
    expect(result.kind).toBe("no_data");
  });

  // Regression: valid metrics body returns kind:ok
  it("returns kind:ok for a valid quality metrics body", async () => {
    mockForward.mockResolvedValue(
      makeJsonResponse({
        faithfulness: 0.87,
        answer_relevancy: 0.92,
        context_precision: 0.75,
        context_recall: 0.81,
        golden_set_version: "v1.2.3",
        finished_at: "2025-01-15T14:32:00Z",
      })
    );
    const result = await fetchQuality("rid-qual-7");
    expect(result.kind).toBe("ok");
  });
});
