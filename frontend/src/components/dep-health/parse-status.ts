/**
 * Pure mapping function: raw gateway JSON → DepHealthResult.
 *
 * Extracted from the Client Component so it can be unit-tested in Vitest
 * without a browser environment. Carries zero React or Next.js imports.
 *
 * Gateway contract (GET /v1/status — OBS-002 / status.rs, extended by #253):
 *   {
 *     status: "ok"|"degraded",
 *     dependencies: {
 *       postgres: { status: "ok"|"down", latency_ms: number },
 *       gcs:      { status: "ok"|"down", latency_ms: number },
 *       workers:  { status: "ok"|"dormant"|"down", latency_ms: number },
 *     },
 *     checked_at: string, // RFC3339
 *   }
 *
 * #253: Workers is scale-to-zero. "dormant" means ready-to-serve but cold
 * (Cloud Run Ready=True) — a third, healthy state, never collapsed to "down".
 * Only Workers may be "dormant"; postgres/gcs stay binary ok|down.
 */

/** A binary dependency status — only "ok" is healthy, everything else is down. */
export type DepStatusValue = "ok" | "down";

/** Workers' tri-state status — "dormant" is healthy (scale-to-zero), never red (#253). */
export type WorkersStatusValue = "ok" | "dormant" | "down";

/** Successful parse of the gateway /v1/status body. */
export interface DepHealthOk {
  kind: "ok";
  postgres: DepStatusValue;
  gcs: DepStatusValue;
  workers: WorkersStatusValue;
  checked_at: string;
}

/** Failed parse — body shape was unexpected. */
export interface DepHealthError {
  kind: "error";
}

export type DepHealthResult = DepHealthOk | DepHealthError;

/** Coerce an unknown dep status string to the binary DepStatusValue type. */
function toDepStatusValue(value: unknown): DepStatusValue {
  return value === "ok" ? "ok" : "down";
}

/** Coerce an unknown workers status string to the tri-state WorkersStatusValue (#253). */
function toWorkersStatusValue(value: unknown): WorkersStatusValue {
  if (value === "ok") {
    return "ok";
  }
  if (value === "dormant") {
    return "dormant";
  }
  return "down";
}

function isDepObject(value: unknown): value is { status: unknown } {
  return typeof value === "object" && value !== null && "status" in value;
}

/**
 * Parses the raw body returned by GET /api/v1/status.
 *
 * Returns kind:"error" for any unexpected shape — never throws.
 * AC1: postgres/gcs map unambiguously to "ok" or "down"; workers also to "dormant" (#253).
 */
export function parseStatusBody(body: unknown): DepHealthResult {
  if (typeof body !== "object" || body === null) {
    return { kind: "error" };
  }

  const b = body as Record<string, unknown>;

  if (typeof b["dependencies"] !== "object" || b["dependencies"] === null) {
    return { kind: "error" };
  }

  const deps = b["dependencies"] as Record<string, unknown>;

  if (!isDepObject(deps["postgres"]) || !isDepObject(deps["gcs"]) || !isDepObject(deps["workers"])) {
    return { kind: "error" };
  }

  return {
    kind: "ok",
    postgres: toDepStatusValue(deps["postgres"].status),
    gcs: toDepStatusValue(deps["gcs"].status),
    workers: toWorkersStatusValue(deps["workers"].status),
    checked_at: typeof b["checked_at"] === "string" ? b["checked_at"] : "",
  };
}
