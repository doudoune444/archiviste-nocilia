// AC: AUTH-001
// AC: 401 → invalid-credentials French message
// AC: 429 → throttled French message WITH wait hint in seconds
// AC: 503 → upstream-unavailable French message
// AC: sub-minimum password rejected before submit

// AC: AUTH-002
// AC: 409 → email-already-registered French message directing to login
// AC: sub-minimum password rejected client-side before submit (inherited from AUTH-001)

import { describe, it, expect } from "vitest";
import {
  mapGatewayStatusToMessage,
  mapSignupStatusToMessage,
  isPasswordLongEnough,
  PASSWORD_MIN_LEN,
} from "@/lib/auth-forms";

describe("isPasswordLongEnough()", () => {
  // AC: sub-minimum password rejected client-side before submit
  it("rejects a password shorter than PASSWORD_MIN_LEN", () => {
    expect(isPasswordLongEnough("short")).toBe(false);
  });

  it(`rejects a password of exactly ${PASSWORD_MIN_LEN - 1} characters`, () => {
    const tooShort = "a".repeat(PASSWORD_MIN_LEN - 1);
    expect(isPasswordLongEnough(tooShort)).toBe(false);
  });

  it(`accepts a password of exactly ${PASSWORD_MIN_LEN} characters`, () => {
    const atMinimum = "a".repeat(PASSWORD_MIN_LEN);
    expect(isPasswordLongEnough(atMinimum)).toBe(true);
  });

  it("accepts a password longer than the minimum", () => {
    expect(isPasswordLongEnough("correct-horse-battery")).toBe(true);
  });
});

describe("mapGatewayStatusToMessage()", () => {
  // AC: 401 → invalid-credentials French message
  it("maps 401 to a French invalid-credentials message", () => {
    const result = mapGatewayStatusToMessage(
      401,
      { error: "invalid_credentials", request_id: "req-1" },
      null
    );
    expect(result.message).toContain("mot de passe");
    expect(result.retryAfterSeconds).toBeUndefined();
  });

  it("maps 401 to the same message regardless of body shape", () => {
    const result = mapGatewayStatusToMessage(401, null, null);
    expect(result.message).toBeTruthy();
    expect(result.message.length).toBeGreaterThan(0);
  });

  // AC: 429 → throttled French message WITH wait hint
  it("maps 429 to a French throttled message including the wait hint from body", () => {
    const result = mapGatewayStatusToMessage(
      429,
      { error: "login_throttled", request_id: "req-2", retry_after_seconds: 300 },
      "300"
    );
    expect(result.message).toContain("300");
    expect(result.retryAfterSeconds).toBe(300);
  });

  it("maps 429 and reads retry_after_seconds from the body field", () => {
    const result = mapGatewayStatusToMessage(
      429,
      { error: "login_throttled", request_id: "req-3", retry_after_seconds: 120 },
      null
    );
    expect(result.retryAfterSeconds).toBe(120);
    expect(result.message).toContain("120");
  });

  it("maps 429 and falls back to Retry-After header when body field is absent", () => {
    const result = mapGatewayStatusToMessage(
      429,
      { error: "login_throttled", request_id: "req-4" },
      "60"
    );
    expect(result.retryAfterSeconds).toBe(60);
    expect(result.message).toContain("60");
  });

  it("maps 429 without a wait hint when neither body nor header has one", () => {
    const result = mapGatewayStatusToMessage(
      429,
      { error: "login_throttled", request_id: "req-5" },
      null
    );
    expect(result.message).toBeTruthy();
    expect(result.retryAfterSeconds).toBeUndefined();
  });

  // AC: 503 → upstream-unavailable French message
  it("maps 503 to a French upstream-unavailable message", () => {
    const result = mapGatewayStatusToMessage(
      503,
      { error: "upstream_unavailable", request_id: "req-6" },
      null
    );
    expect(result.message).toContain("indisponible");
    expect(result.retryAfterSeconds).toBeUndefined();
  });

  it("maps an unexpected status to a generic French fallback", () => {
    const result = mapGatewayStatusToMessage(500, {}, null);
    expect(result.message).toBeTruthy();
    expect(result.message.length).toBeGreaterThan(0);
  });

  // A09: gateway error code must not appear verbatim in the returned message
  it("does not expose raw gateway error codes in the French message", () => {
    const result401 = mapGatewayStatusToMessage(
      401,
      { error: "invalid_credentials" },
      null
    );
    expect(result401.message).not.toContain("invalid_credentials");

    const result429 = mapGatewayStatusToMessage(
      429,
      { error: "login_throttled", retry_after_seconds: 30 },
      null
    );
    expect(result429.message).not.toContain("login_throttled");

    const result503 = mapGatewayStatusToMessage(
      503,
      { error: "upstream_unavailable" },
      null
    );
    expect(result503.message).not.toContain("upstream_unavailable");
  });
});

describe("mapSignupStatusToMessage()", () => {
  // AC AUTH-002: 409 → email-already-registered French message directing to login
  it("maps 409 to a French email-already-registered message", () => {
    const result = mapSignupStatusToMessage(
      409,
      { error: "email_taken", request_id: "req-7" },
      null
    );
    expect(result.message).toContain("déjà");
    expect(result.retryAfterSeconds).toBeUndefined();
  });

  it("maps 409 and directs the user to log in", () => {
    const result = mapSignupStatusToMessage(
      409,
      { error: "email_taken", request_id: "req-8" },
      null
    );
    // AC AUTH-002: message must direct user to log in
    expect(result.message.toLowerCase()).toMatch(/connect/);
  });

  it("does not expose the raw email_taken error code in the message", () => {
    const result = mapSignupStatusToMessage(
      409,
      { error: "email_taken" },
      null
    );
    expect(result.message).not.toContain("email_taken");
  });

  // Non-409 errors delegate to the shared mapping behavior
  it("maps 503 via signup to a French upstream-unavailable message", () => {
    const result = mapSignupStatusToMessage(
      503,
      { error: "upstream_unavailable" },
      null
    );
    expect(result.message).toContain("indisponible");
  });

  it("maps an unexpected status via signup to a generic French fallback", () => {
    const result = mapSignupStatusToMessage(500, {}, null);
    expect(result.message).toBeTruthy();
    expect(result.message.length).toBeGreaterThan(0);
  });
});
