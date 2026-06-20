/**
 * DASH-001 AC-2 — Dashboard forbidden / refusal path.
 *
 * Verifies that when the tickets fetch returns 401 or 403, the page renders the
 * "réservé à l'auteur" refusal message and NOT a broken page or stack trace.
 *
 * The e2e spec asserts the heading is always present, but never asserts that the
 * refusal text renders for a real 403 response. This unit test fills that gap by
 * mocking forward() to return 401 / 403, confirming the RSC branches to the
 * refusal UI path.
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
vi.mock("@/app/dashboard/page.module.css", () => ({ default: {} }));

// Mock the BoardControls client component (uses useRouter — unavailable in jsdom RSC tests).
vi.mock("@/components/category-filter/BoardControls", () => ({
  BoardControls: () => null,
}));

// Mock LoadMoreButton (client component — not under test here).
vi.mock("@/components/board/LoadMoreButton", () => ({
  LoadMoreButton: () => null,
}));

// Import after mocks are registered.
import { forward } from "@/lib/bff-proxy";

// Lazy-import the page so mocks are in place before module evaluation.
const { default: DashboardPage } = await import("@/app/dashboard/page");

const forwardMock = forward as ReturnType<typeof vi.fn>;

describe("DashboardPage — AC-2 refusal path (DASH-001)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  // AC-2: 403 from gateway → "réservé à l'auteur" refusal message, no broken page.
  it("renders refusal message when forward() returns 403 author_required", async () => {
    // DASH-001 AC-2: non-author (gateway 403) → clean refusal, not a broken page
    forwardMock.mockResolvedValue(
      new Response(JSON.stringify({ error: "author_required" }), {
        status: 403,
        headers: { "content-type": "application/json" },
      })
    );

    const element = await DashboardPage({
      searchParams: Promise.resolve({}),
    });
    render(element);

    // AC-2: the refusal text must be present (role="status" on the <p>).
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.getByRole("status").textContent).toMatch(/auteur/i);

    // AC-2: no error alert (error alert is for load failures, not auth refusals).
    expect(screen.queryByTestId("dashboard-error")).not.toBeInTheDocument();
  });

  // AC-2: 401 from gateway (unauthenticated) → same clean refusal.
  it("renders refusal message when forward() returns 401 invalid_token", async () => {
    // DASH-001 AC-2: unauthenticated caller (gateway 401) → same refusal as 403
    forwardMock.mockResolvedValue(
      new Response(JSON.stringify({ error: "invalid_token" }), {
        status: 401,
        headers: { "content-type": "application/json" },
      })
    );

    const element = await DashboardPage({
      searchParams: Promise.resolve({}),
    });
    render(element);

    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.getByRole("status").textContent).toMatch(/auteur/i);
    expect(screen.queryByTestId("dashboard-error")).not.toBeInTheDocument();
  });
});
