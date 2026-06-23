// AC #245: the root route `/` is the chat — the hero with the "/chat" CTA is
// gone. The page is a trivial passthrough; the chat surface itself is rendered
// by the global AppShell on the / route (asserted in app-shell.test.tsx and
// app-shell-server.test.tsx). This test only pins that the old hero CTA link no
// longer exists.

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import AccueilPage from "@/app/page";

describe("AccueilPage — root is the chat, no hero CTA (#245)", () => {
  it("does not render a link to /chat", () => {
    const { container } = render(<AccueilPage />);
    expect(
      screen.queryByRole("link", { name: /interroger l'archiviste/i })
    ).not.toBeInTheDocument();
    // No leftover hero markup either.
    expect(container.querySelector("a[href='/chat']")).toBeNull();
  });
});
