// AC #245 — AppShellServer fetches identity + conversations server-side and
// renders the client AppShell. It degrades to anonymous + empty history on any
// failure (fail-soft), and never crashes the layout.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";

vi.mock("next/headers", () => ({
  cookies: vi.fn().mockResolvedValue({ toString: () => "" }),
  headers: vi.fn().mockResolvedValue({ get: () => null }),
}));

const mockForward = vi.fn<(req: Request, path: string) => Promise<Response>>();
vi.mock("@/lib/bff-proxy", () => ({
  forward: (req: Request, path: string) => mockForward(req, path),
}));

// Stub the client AppShell so the server component test asserts the props it
// passes, not the full client behavior (covered in app-shell.test.tsx).
vi.mock("@/components/app-shell/AppShell", () => ({
  AppShell: (props: {
    tier: string;
    email: string | null;
    initialConversations: unknown[];
    children: React.ReactNode;
  }) => (
    <div
      data-testid="app-shell"
      data-tier={props.tier}
      data-email={props.email ?? ""}
      data-conv-count={props.initialConversations.length}
    >
      {props.children}
    </div>
  ),
}));

import { AppShellServer } from "@/components/app-shell/AppShellServer";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function renderShell() {
  const element = await AppShellServer({ children: <div>enfant</div> });
  render(element as React.ReactElement);
}

beforeEach(() => {
  mockForward.mockReset();
});

describe("AppShellServer (#245)", () => {
  it("passes the fetched tier/email and conversation list to AppShell", async () => {
    mockForward.mockImplementation((_req, path) => {
      if (path === "/v1/me") {
        return Promise.resolve(
          jsonResponse({ tier: "member", email: "m@e.com" })
        );
      }
      return Promise.resolve(
        jsonResponse({
          conversations: [
            {
              id: "c1",
              created_at: "2026-01-01T00:00:00Z",
              updated_at: "2026-01-02T00:00:00Z",
              message_count: 2,
              title: "Qui est Blowen ?",
            },
          ],
        })
      );
    });

    await renderShell();
    const shell = screen.getByTestId("app-shell");
    expect(shell).toHaveAttribute("data-tier", "member");
    expect(shell).toHaveAttribute("data-email", "m@e.com");
    expect(shell).toHaveAttribute("data-conv-count", "1");
    expect(screen.getByText("enfant")).toBeInTheDocument();
  });

  it("degrades to anonymous + empty history when /v1/me fails", async () => {
    mockForward.mockRejectedValue(new Error("no gateway"));
    await renderShell();
    const shell = screen.getByTestId("app-shell");
    expect(shell).toHaveAttribute("data-tier", "anonymous");
    expect(shell).toHaveAttribute("data-conv-count", "0");
  });
});
