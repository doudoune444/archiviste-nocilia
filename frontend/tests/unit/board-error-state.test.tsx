/**
 * BOARD-002 AC5 — Board RSC error state tests.
 *
 * Verifies that fetchInitialBoard() routes to the error UI (data-testid="board-error")
 * in all failure modes:
 *   - forward() throws (network error, AbortSignal timeout, unset GATEWAY_URL)
 *   - forward() returns a non-ok response
 *   - forward() returns 200 with a malformed body (res.json() returns {})
 *   - forward() returns 200 with a body whose res.json() itself throws
 *
 * Mocks at the forward() boundary so we never make real network calls.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";

// Mock next/headers (server-only module — unavailable in jsdom).
vi.mock("next/headers", () => ({
  cookies: vi.fn().mockResolvedValue({ toString: () => "" }),
  headers: vi.fn().mockResolvedValue({ get: () => null }),
}));

// Mock the bff-proxy forward function.
vi.mock("@/lib/bff-proxy", () => ({
  forward: vi.fn(),
}));

// Mock CSS modules.
vi.mock("@/app/board/page.module.css", () => ({ default: {} }));

// Import after mocks are registered.
import { forward } from "@/lib/bff-proxy";

// Lazy-import the page so mocks are in place before module evaluation.
const { default: BoardPage } = await import("@/app/board/page");

const forwardMock = forward as ReturnType<typeof vi.fn>;

describe("BoardPage — AC5 error state (BOARD-002)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  // AC5: forward() throws (network error, AbortSignal timeout, unset GATEWAY_URL)
  it("renders board error state when forward() throws", async () => {
    forwardMock.mockRejectedValue(new Error("Network failure"));

    const element = await BoardPage();
    render(element);

    const errorEl = screen.getByTestId("board-error");
    expect(errorEl).toBeInTheDocument();
    expect(errorEl).toHaveTextContent("Impossible de charger les tickets.");
    // A request id must be shown (generated fallback UUID when no response).
    expect(errorEl.textContent).toMatch(/id\s*:/);
  });

  // AC5: gateway returns a non-ok status
  it("renders board error state when forward() returns a non-ok response", async () => {
    forwardMock.mockResolvedValue(
      new Response(null, {
        status: 503,
        headers: { "x-request-id": "req-503-test" },
      })
    );

    const element = await BoardPage();
    render(element);

    const errorEl = screen.getByTestId("board-error");
    expect(errorEl).toBeInTheDocument();
    expect(errorEl).toHaveTextContent("Impossible de charger les tickets.");
    expect(errorEl.textContent).toContain("req-503-test");
  });

  // AC5: forward() returns 200 with a malformed body (missing items/total)
  it("renders board error state when 200 body fails isBoardPage guard", async () => {
    forwardMock.mockResolvedValue(
      new Response(JSON.stringify({}), {
        status: 200,
        headers: {
          "content-type": "application/json",
          "x-request-id": "req-malformed",
        },
      })
    );

    const element = await BoardPage();
    render(element);

    const errorEl = screen.getByTestId("board-error");
    expect(errorEl).toBeInTheDocument();
    expect(errorEl.textContent).toContain("req-malformed");
  });

  // AC5: forward() returns 200 but res.json() throws (malformed JSON body)
  it("renders board error state when res.json() throws on a 200 response", async () => {
    const brokenBody = new Response("not-json{{{", {
      status: 200,
      headers: {
        "content-type": "application/json",
        "x-request-id": "req-broken-json",
      },
    });

    forwardMock.mockResolvedValue(brokenBody);

    const element = await BoardPage();
    render(element);

    const errorEl = screen.getByTestId("board-error");
    expect(errorEl).toBeInTheDocument();
    // Request id from the response headers is used when available.
    expect(errorEl.textContent).toContain("req-broken-json");
  });
});
