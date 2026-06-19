// AC: AUTH-001 security fix — open-redirect guard on ?from= param
// AC: same-origin relative paths accepted; external/protocol-relative/backslash rejected → /

import { describe, it, expect } from "vitest";
import { safeRedirectTarget } from "@/lib/auth-forms";

describe("safeRedirectTarget()", () => {
  // Valid same-origin relative paths must pass through unchanged.
  it("accepts a simple same-origin relative path", () => {
    expect(safeRedirectTarget("/board")).toBe("/board");
  });

  it("accepts a nested relative path", () => {
    expect(safeRedirectTarget("/conversations/42")).toBe("/conversations/42");
  });

  it("accepts the root path /", () => {
    expect(safeRedirectTarget("/")).toBe("/");
  });

  // Protocol-relative paths (//) are treated as external by browsers.
  it("rejects a protocol-relative URL starting with //", () => {
    expect(safeRedirectTarget("//evil.com")).toBe("/");
  });

  // Backslash trick: some browsers normalise /\ to // before navigation.
  it("rejects a backslash-trick path /\\evil.com", () => {
    expect(safeRedirectTarget("/\\evil.com")).toBe("/");
  });

  // Absolute external URLs must be rejected.
  it("rejects an absolute https URL", () => {
    expect(safeRedirectTarget("https://evil.com")).toBe("/");
  });

  it("rejects an absolute http URL", () => {
    expect(safeRedirectTarget("http://evil.com/steal")).toBe("/");
  });

  // javascript: is a common XSS vector passed via redirect params.
  it("rejects a javascript: scheme", () => {
    expect(safeRedirectTarget("javascript:alert(1)")).toBe("/");
  });

  // Empty and null/undefined must default to /.
  it("defaults to / for an empty string", () => {
    expect(safeRedirectTarget("")).toBe("/");
  });

  it("defaults to / for null", () => {
    expect(safeRedirectTarget(null)).toBe("/");
  });

  it("defaults to / for undefined", () => {
    expect(safeRedirectTarget(undefined)).toBe("/");
  });
});
