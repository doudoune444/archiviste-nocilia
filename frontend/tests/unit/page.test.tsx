// AC #247: landing on "/" renders the chat shell directly (no hero / CTA).
// The root page is a server component that fetches the initial conversation
// list via bff-proxy and renders ChatShell. On fetch failure it fails soft
// (empty sidebar) and the chat surface is still usable.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";

// next/headers is unavailable in jsdom — stub cookies()/headers().
vi.mock("next/headers", () => ({
  cookies: vi.fn().mockResolvedValue({ toString: () => "" }),
  headers: vi.fn().mockResolvedValue({ get: () => null }),
}));

// bff-proxy forward() — controlled per test.
const mockForward = vi.fn<(req: Request, path: string) => Promise<Response>>();
vi.mock("@/lib/bff-proxy", () => ({
  forward: (req: Request, path: string) => mockForward(req, path),
}));

// CSS modules pulled in transitively by ChatForm / ConversationHistory.
vi.mock("@/components/chat/chat.module.css", () => ({
  default: new Proxy({}, { get: (_t, prop: string) => prop }),
}));
vi.mock(
  "@/components/conversation-history/ConversationHistory.module.css",
  () => ({ default: new Proxy({}, { get: (_t, prop: string) => prop }) })
);

import AccueilPage from "@/app/page";

beforeEach(() => {
  mockForward.mockResolvedValue(
    new Response(JSON.stringify({ conversations: [] }), {
      status: 200,
      headers: { "content-type": "application/json" },
    })
  );
});

describe("AccueilPage — chat at the root (#247)", () => {
  it("renders the chat input directly, with no welcome CTA", async () => {
    const element = await AccueilPage();
    render(element as React.ReactElement);

    expect(
      screen.getByRole("textbox", { name: /votre question/i })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /Interroger l'archiviste/i })
    ).not.toBeInTheDocument();
  });

  it("renders the chat shell even when the conversation list fetch fails (fail-soft)", async () => {
    mockForward.mockRejectedValue(new Error("gateway down"));
    const element = await AccueilPage();
    render(element as React.ReactElement);

    expect(
      screen.getByRole("textbox", { name: /votre question/i })
    ).toBeInTheDocument();
  });
});
