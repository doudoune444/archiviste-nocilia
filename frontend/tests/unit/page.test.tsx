// AC #247: landing on `/` renders the chat surface directly (no hero/CTA page).
// The root page is now an async Server Component that fetches the initial
// conversation list via bff-proxy and renders the ChatShell (sidebar + ChatForm).

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";

// next/headers — cookies()/headers() are unavailable in jsdom.
vi.mock("next/headers", () => ({
  cookies: vi.fn().mockResolvedValue({ toString: () => "" }),
  headers: vi.fn().mockResolvedValue({ get: () => null }),
}));

// bff-proxy — return an empty conversation list so the sidebar starts empty.
vi.mock("@/lib/bff-proxy", () => ({
  forward: vi
    .fn()
    .mockResolvedValue(
      new Response(JSON.stringify({ conversations: [] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      })
    ),
}));

// CSS modules — jsdom cannot process real CSS; stub with identity proxies.
vi.mock("@/components/chat/chat.module.css", () => ({
  default: new Proxy({}, { get: (_t, prop: string) => prop }),
}));
vi.mock(
  "@/components/conversation-history/ConversationHistory.module.css",
  () => ({ default: new Proxy({}, { get: (_t, prop: string) => prop }) })
);

import HomePage from "@/app/page";
import { SidebarChatProvider } from "@/components/app-sidebar/SidebarChatContext";

async function renderHome() {
  // HomePage is an async server component; await it before rendering.
  // ChatShell registers into the sidebar context (#248), so wrap in the provider.
  const element = await HomePage();
  render(
    <SidebarChatProvider>{element as React.ReactElement}</SidebarChatProvider>
  );
}

describe("HomePage (/) renders the chat surface", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the chat question textarea directly", async () => {
    await renderHome();
    expect(
      screen.getByRole("textbox", { name: /question/i })
    ).toBeInTheDocument();
  });

  it("renders the send button", async () => {
    await renderHome();
    expect(
      screen.getByRole("button", { name: /envoyer/i })
    ).toBeInTheDocument();
  });

  it("does not render the former welcome hero CTA", async () => {
    await renderHome();
    expect(
      screen.queryByRole("link", { name: /Interroger l'archiviste/i })
    ).not.toBeInTheDocument();
  });
});
