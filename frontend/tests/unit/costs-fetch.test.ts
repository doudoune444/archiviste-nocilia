// Fetch-layer tests for the Coûts card (#275) — fetchCosts boundary validation.
//
// A malformed-200 body, a thrown rejection, or a non-200 must each produce
// kind:"error" and never throw. Mocks at the `forward` boundary so no real
// HTTP calls are made. Mirrors observability-fetch.test.ts.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

vi.mock("next/headers", () => ({
  headers: vi.fn().mockResolvedValue(new Headers()),
}));

vi.mock("@/lib/bff-proxy", () => ({
  forward: vi.fn(),
}));

import { fetchCosts } from "@/app/metriques/fetch";
import { forward } from "@/lib/bff-proxy";
const mockForward = vi.mocked(forward);

const TEST_RID = "req-cost-001";

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "x-request-id": TEST_RID },
  });
}

const VALID_BODY = {
  currency: "EUR",
  period: "rolling_30d",
  estimated: true,
  total_eur: 12.34,
  services: { postgres: 8.0, gcs: 0.5, workers: 3.84 },
  computed_at: "2026-06-24T10:00:00+00:00",
};

beforeEach(() => {
  vi.resetAllMocks();
});
afterEach(() => {
  vi.resetAllMocks();
});

describe("fetchCosts", () => {
  it("returns kind:ok for a valid costs body", async () => {
    mockForward.mockResolvedValue(makeJsonResponse(VALID_BODY));
    const result = await fetchCosts("rid-1");
    expect(result.kind).toBe("ok");
    if (result.kind === "ok") {
      expect(result.total_eur).toBe(12.34);
      expect(result.services.postgres).toBe(8.0);
      expect(result.services.gcs).toBe(0.5);
      expect(result.services.workers).toBe(3.84);
    }
  });

  it("returns kind:error for a 200 body missing total_eur", async () => {
    const { total_eur, ...rest } = VALID_BODY;
    void total_eur;
    mockForward.mockResolvedValue(makeJsonResponse(rest));
    const result = await fetchCosts("rid-2");
    expect(result.kind).toBe("error");
  });

  it("returns kind:error when a service amount is non-finite", async () => {
    mockForward.mockResolvedValue(
      makeJsonResponse({ ...VALID_BODY, services: { postgres: 8.0, gcs: null, workers: 3.84 } })
    );
    const result = await fetchCosts("rid-3");
    expect(result.kind).toBe("error");
  });

  it("returns kind:error when services object is absent", async () => {
    const { services, ...rest } = VALID_BODY;
    void services;
    mockForward.mockResolvedValue(makeJsonResponse(rest));
    const result = await fetchCosts("rid-4");
    expect(result.kind).toBe("error");
  });

  it("returns kind:error when forward rejects (network failure)", async () => {
    mockForward.mockRejectedValue(new Error("network down"));
    const result = await fetchCosts("rid-5");
    expect(result.kind).toBe("error");
  });

  it("returns kind:error for a non-200 response", async () => {
    mockForward.mockResolvedValue(makeJsonResponse({ error: "upstream_unavailable" }, 503));
    const result = await fetchCosts("rid-6");
    expect(result.kind).toBe("error");
  });

  it("surfaces the request_id from the gateway error body (#277)", async () => {
    // Gateway error envelope carries the request_id in the JSON body; no
    // x-request-id header is set on the response. The diagnosable id must be
    // the gateway's, not the caller's fallback.
    const res = new Response(
      JSON.stringify({ error: "cost_config_unavailable", request_id: "gw-req-77" }),
      { status: 503, headers: { "content-type": "application/json" } }
    );
    mockForward.mockResolvedValue(res);
    const result = await fetchCosts("caller-fallback");
    expect(result.kind).toBe("error");
    if (result.kind === "error") {
      expect(result.request_id).toBe("gw-req-77");
    }
  });
});
