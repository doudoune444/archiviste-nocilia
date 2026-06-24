/**
 * Observability fetch layer — WEBOBS-001.
 *
 * Server-side signal fetchers for the observability page. Extracted from
 * page.tsx because Next.js 15 type-checks page files and rejects any named
 * export that is not a reserved Next.js name (default, metadata, …). These
 * helpers must live outside the page module to be exported and unit-tested.
 *
 * Each fetcher constructs a synthetic server-side Request (public endpoints,
 * no browser cookies) and calls forward() independently so one failure does
 * not prevent the other signal from rendering (AC5).
 */
import { forward } from "@/lib/bff-proxy";
import type {
  StatsResult,
  QualityResult,
  QualityMetrics,
  CostsResult,
  CostsResponse,
} from "@/lib/observability-types";

/** Returns true when body carries a finite conversation_count. */
function isValidStatsBody(body: unknown): body is { conversation_count: number } {
  return (
    typeof body === "object" &&
    body !== null &&
    typeof (body as Record<string, unknown>)["conversation_count"] === "number" &&
    Number.isFinite((body as Record<string, unknown>)["conversation_count"] as number)
  );
}

/** Returns true when body carries all four finite Ragas scores and required strings. */
function isValidQualityMetrics(body: unknown): body is QualityMetrics {
  if (typeof body !== "object" || body === null) return false;
  const b = body as Record<string, unknown>;
  const scores: Array<string> = [
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
  ];
  const allScoresFinite = scores.every(
    (k) => typeof b[k] === "number" && Number.isFinite(b[k] as number)
  );
  const hasStrings =
    typeof b["golden_set_version"] === "string" &&
    typeof b["finished_at"] === "string";
  return allScoresFinite && hasStrings;
}

/** Returns true when value is a finite number. */
function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** Returns true when body carries finite total + three finite service amounts. */
function isValidCostsBody(body: unknown): body is CostsResponse {
  if (typeof body !== "object" || body === null) return false;
  const b = body as Record<string, unknown>;
  if (!isFiniteNumber(b["total_eur"])) return false;
  const services = b["services"];
  if (typeof services !== "object" || services === null) return false;
  const s = services as Record<string, unknown>;
  return (
    isFiniteNumber(s["postgres"]) &&
    isFiniteNumber(s["gcs"]) &&
    isFiniteNumber(s["workers"])
  );
}

/** Extract a string `request_id` from a parsed error body, if present. */
function requestIdFromBody(body: unknown): string | null {
  if (typeof body !== "object" || body === null) return null;
  const value = (body as Record<string, unknown>)["request_id"];
  return typeof value === "string" ? value : null;
}

export async function fetchCosts(requestId: string): Promise<CostsResult> {
  const req = new Request("http://internal/v1/costs", {
    headers: { "x-request-id": requestId },
  });
  try {
    const res = await forward(req, "/v1/costs");
    const rid = res.headers.get("x-request-id") ?? requestId;
    const body: unknown = await res.json();
    if (!res.ok) {
      // The gateway error envelope carries its own diagnosable request_id in
      // the body (#277); prefer it over the caller fallback.
      return { kind: "error", request_id: requestIdFromBody(body) ?? rid };
    }
    if (!isValidCostsBody(body)) {
      return { kind: "error", request_id: rid };
    }
    return { kind: "ok", ...body };
  } catch {
    return { kind: "error", request_id: requestId };
  }
}

export async function fetchStats(requestId: string): Promise<StatsResult> {
  const req = new Request("http://internal/v1/stats", {
    headers: { "x-request-id": requestId },
  });
  try {
    const res = await forward(req, "/v1/stats");
    const rid = res.headers.get("x-request-id") ?? requestId;
    if (!res.ok) {
      return { kind: "error", request_id: rid };
    }
    const body: unknown = await res.json();
    if (!isValidStatsBody(body)) {
      return { kind: "error", request_id: rid };
    }
    return { kind: "ok", conversation_count: body.conversation_count };
  } catch {
    return { kind: "error", request_id: requestId };
  }
}

export async function fetchQuality(requestId: string): Promise<QualityResult> {
  const req = new Request("http://internal/v1/quality", {
    headers: { "x-request-id": requestId },
  });
  try {
    const res = await forward(req, "/v1/quality");
    const rid = res.headers.get("x-request-id") ?? requestId;
    if (!res.ok) {
      return { kind: "error", request_id: rid };
    }
    const body: unknown = await res.json();
    // Check no_data first: gateway returns {"status":"no_data"} when no eval has run.
    if (
      typeof body === "object" &&
      body !== null &&
      (body as Record<string, unknown>)["status"] === "no_data"
    ) {
      return { kind: "no_data" };
    }
    if (!isValidQualityMetrics(body)) {
      return { kind: "error", request_id: rid };
    }
    return { kind: "ok", ...body };
  } catch {
    return { kind: "error", request_id: requestId };
  }
}
