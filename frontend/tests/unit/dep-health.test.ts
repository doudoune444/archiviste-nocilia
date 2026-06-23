// Unit tests for dep-health status parsing — WEBOBS-002
//
// AC1: postgres, gcs, and workers each render with an unambiguous healthy/down status.
// AC4: The island fits the existing observability signal-card layout.
//
// These tests cover the pure mapping function from gateway JSON → DepHealthResult.
// The component itself is tested via Playwright smoke (e2e).

import { describe, it, expect } from "vitest";
import {
  parseStatusBody,
  type DepHealthResult,
} from "@/components/dep-health/parse-status";

const VALID_BODY = {
  status: "ok",
  dependencies: {
    postgres: { status: "ok", latency_ms: 3 },
    gcs: { status: "ok", latency_ms: 12 },
    workers: { status: "ok", latency_ms: 8 },
  },
  checked_at: "2026-06-19T10:00:00Z",
};

describe("parseStatusBody", () => {
  // AC1: valid healthy response maps all three deps to "ok"
  it("maps a healthy gateway response to kind:ok with all deps ok", () => {
    const result: DepHealthResult = parseStatusBody(VALID_BODY);
    expect(result.kind).toBe("ok");
    if (result.kind === "ok") {
      expect(result.postgres).toBe("ok");
      expect(result.gcs).toBe("ok");
      expect(result.workers).toBe("ok");
    }
  });

  // AC1: degraded response maps degraded deps to "down"
  it("maps a degraded response where postgres is down", () => {
    const body = {
      ...VALID_BODY,
      status: "degraded",
      dependencies: {
        ...VALID_BODY.dependencies,
        postgres: { status: "down", latency_ms: 0 },
      },
    };
    const result = parseStatusBody(body);
    expect(result.kind).toBe("ok");
    if (result.kind === "ok") {
      expect(result.postgres).toBe("down");
      expect(result.gcs).toBe("ok");
      expect(result.workers).toBe("ok");
    }
  });

  // #253: workers "dormant" (Cloud Run Ready=True, scale-to-zero) is parsed as a
  // distinct healthy third state — never collapsed to "down".
  it("maps workers dormant to the dormant state", () => {
    const body = {
      ...VALID_BODY,
      status: "ok",
      dependencies: {
        ...VALID_BODY.dependencies,
        workers: { status: "dormant", latency_ms: 5 },
      },
    };
    const result = parseStatusBody(body);
    expect(result.kind).toBe("ok");
    if (result.kind === "ok") {
      expect(result.workers).toBe("dormant");
      expect(result.postgres).toBe("ok");
      expect(result.gcs).toBe("ok");
    }
  });

  // #253: postgres/gcs never carry "dormant"; an unexpected dormant there → down.
  it("treats dormant on postgres/gcs as down (only workers may be dormant)", () => {
    const body = {
      ...VALID_BODY,
      dependencies: {
        ...VALID_BODY.dependencies,
        postgres: { status: "dormant", latency_ms: 0 },
      },
    };
    const result = parseStatusBody(body);
    if (result.kind === "ok") {
      expect(result.postgres).toBe("down");
    }
  });

  // AC1: unknown dep status string treated as "down" (unambiguous)
  it("treats unknown dep status string as down", () => {
    const body = {
      ...VALID_BODY,
      dependencies: {
        ...VALID_BODY.dependencies,
        workers: { status: "unknown", latency_ms: 0 },
      },
    };
    const result = parseStatusBody(body);
    if (result.kind === "ok") {
      expect(result.workers).toBe("down");
    }
  });

  // AC1: missing dependencies key → kind:error
  it("returns kind:error when body is missing dependencies key", () => {
    const result = parseStatusBody({ status: "ok" });
    expect(result.kind).toBe("error");
  });

  // AC1: null body → kind:error, never throws
  it("returns kind:error for null body, never throws", () => {
    const result = parseStatusBody(null);
    expect(result.kind).toBe("error");
  });

  // AC1: non-object body → kind:error
  it("returns kind:error for a string body", () => {
    const result = parseStatusBody("error string");
    expect(result.kind).toBe("error");
  });

  // AC1: missing individual dep → kind:error
  it("returns kind:error when a dep key is missing", () => {
    const body = {
      status: "ok",
      dependencies: {
        postgres: { status: "ok", latency_ms: 3 },
        // gcs missing
        workers: { status: "ok", latency_ms: 8 },
      },
      checked_at: "2026-06-19T10:00:00Z",
    };
    const result = parseStatusBody(body);
    expect(result.kind).toBe("error");
  });
});
