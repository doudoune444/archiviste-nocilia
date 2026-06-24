/**
 * Shared TypeScript types for the observability page (WEBOBS-001).
 *
 * Models the two public gateway contracts as discriminated unions so illegal
 * states (e.g. missing scores on a successful response) are unrepresentable.
 *
 * GET /v1/stats  → StatsResponse
 * GET /v1/quality → QualityResponse
 *
 * Server components fetch these and map them to the Result wrappers below
 * (which also carry the error variant) before passing to presentational cards.
 */

/** Raw payload from GET /v1/stats */
export interface StatsResponse {
  conversation_count: number;
}

/** Raw payload from GET /v1/quality — the full metrics variant */
export interface QualityMetrics {
  faithfulness: number;
  answer_relevancy: number;
  context_precision: number;
  context_recall: number;
  golden_set_version: string;
  finished_at: string;
}

/** Raw payload from GET /v1/quality — the no-data variant */
export interface QualityNoData {
  status: "no_data";
}

/** Discriminated union over the two /v1/quality shapes */
export type QualityResponse = QualityMetrics | QualityNoData;

export function isQualityNoData(r: QualityResponse): r is QualityNoData {
  return (r as QualityNoData).status === "no_data";
}

/** Per-service amounts in the GET /v1/costs payload (#275). */
export interface CostServices {
  postgres: number;
  gcs: number;
  workers: number;
}

/** Raw payload from GET /v1/costs (#275) — the estimated-cost contract. */
export interface CostsResponse {
  currency: string;
  period: string;
  estimated: boolean;
  total_eur: number;
  services: CostServices;
  computed_at: string;
}

// --- Result wrappers used by presentational components ---
// Each card receives one of these; the error shape carries a request id for
// display (never leaks gateway internals).

export type StatsResult =
  | ({ kind: "ok" } & StatsResponse)
  | { kind: "error"; request_id: string };

export type QualityResult =
  | ({ kind: "ok" } & QualityMetrics)
  | { kind: "no_data" }
  | { kind: "error"; request_id: string };

export type CostsResult =
  | ({ kind: "ok" } & CostsResponse)
  | { kind: "error"; request_id: string };
