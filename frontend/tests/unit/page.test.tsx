// AC #247: landing on `/` renders the chat shell directly (no hero/CTA page).
//
// The root page is an async RSC that fetches the conversation list through the
// bff-proxy and renders the ChatShell. We mock the server-only boundaries
// (next/headers, bff-proxy) so the page evaluates in jsdom, mirroring the
// board-error-state test pattern.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";

// Mock next/headers (server-only module — unavailable in jsdom).
vi.mock("next/headers", () => ({
  cookies: vi.fn().mockResolvedValue({ toString: () => "" }),
  headers: vi.fn().mockResolvedValue({ get: () => null }),
}));

// Mock the bff-proxy forward function — controlled per test.
vi.mock("@/lib/bff-proxy", () => ({
  forward: vi.fn(),
}));

// CSS modules import an identity proxy so jsdom does not choke on real CSS.
vi.mock("@/components/chat/chat.module.css", () => ({
  default: new Proxy({}, { get: (_t, prop: string) => prop }),
}));
vi.mock(
  "@/components/conversation-history/ConversationHistory.module.css",
  () => ({ default: new Proxy({}, { get: (_t, prop: string) => prop }) })
);

import { forward } from "@/lib/bff-proxy";

const forwardMock = forward as ReturnType<typeof vi.fn>;

// Lazy-import the page so the mocks are registered before module evaluation.
const { default: AccueilPage } = await import("@/app/page");

describe("AccueilPage — chat at root (#247)", () => {
  beforeEach(() => {
    forwardMock.mockReset();
  });

  it("renders the chat thread directly with no CTA hero link", async () => {
    // Empty conversation list (fail-soft) — the chat surface still renders.
    forwardMock.mockResolvedValue(
      new Response(JSON.stringify({ conversations: [] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      })
    );

    const element = await AccueilPage();
    render(element as React.ReactElement);

    // The chat input is the primary surface on `/`.
    expect(
      screen.getByRole("textbox", { name: /votre question/i })
    ).toBeInTheDocument();
    // The former hero CTA must be gone.
    expect(
      screen.queryByRole("link", { name: /Interroger l'archiviste/i })
    ).not.toBeInTheDocument();
  });
});
