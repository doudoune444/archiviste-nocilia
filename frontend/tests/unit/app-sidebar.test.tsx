// AC #248 — left sidebar app-shell
//
// Behaviors under test (through the public SidebarShell interface):
// - Brand button opens/closes a popover with the navigation links.
// - Dashboard link is present only for the author tier.
// - Account block reflects auth state (anonymous vs connected).
// - "Nouvelle conversation" resets the thread on the chat page, navigates to / elsewhere.
// - Conversation history shows only when a chat slot is registered (chat page).
// - Mobile drawer opens/closes via a hamburger button.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";

// next/link → plain <a> so role/href assertions work in jsdom.
vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    className,
    onClick,
  }: {
    href: string;
    children: React.ReactNode;
    className?: string;
    onClick?: () => void;
  }) => (
    <a href={href} className={className} onClick={onClick}>
      {children}
    </a>
  ),
}));

// next/navigation → controllable router + pathname per test.
const mockPush = vi.fn<(href: string) => void>();
let currentPathname = "/";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  usePathname: () => currentPathname,
}));

import { SidebarShell } from "@/components/app-sidebar/SidebarShell";
import {
  SidebarChatProvider,
  useRegisterChatSidebar,
} from "@/components/app-sidebar/SidebarChatContext";
import type { Identity } from "@/components/app-sidebar/identity";

beforeEach(() => {
  mockPush.mockReset();
  currentPathname = "/";
});

const ANONYMOUS: Identity = { tier: "anonymous", email: null };
const MEMBER: Identity = { tier: "member", email: "member@example.com" };
const AUTHOR: Identity = { tier: "author", email: "author@example.com" };

function renderShell(identity: Identity) {
  render(
    <SidebarChatProvider>
      <SidebarShell identity={identity} />
    </SidebarChatProvider>
  );
}

/** Test helper component that registers chat-page content into the sidebar. */
function ChatRegistrar({
  history,
  onNew,
}: {
  history: React.ReactNode;
  onNew: () => void;
}) {
  useRegisterChatSidebar({ history, onNewConversation: onNew });
  return null;
}

function renderShellWithChat(
  identity: Identity,
  history: React.ReactNode,
  onNew: () => void
) {
  render(
    <SidebarChatProvider>
      <SidebarShell identity={identity} />
      <ChatRegistrar history={history} onNew={onNew} />
    </SidebarChatProvider>
  );
}

// ---------------------------------------------------------------------------
// Popover navigation
// ---------------------------------------------------------------------------

describe("SidebarShell — brand popover", () => {
  it("hides the navigation links until the brand button is clicked", () => {
    renderShell(ANONYMOUS);
    expect(
      screen.queryByRole("link", { name: "Lacunes" })
    ).not.toBeInTheDocument();
  });

  it("opens a popover with Archiviste, Lacunes and État & métriques links", () => {
    renderShell(ANONYMOUS);
    fireEvent.click(screen.getByRole("button", { name: /archiviste nocilia/i }));

    expect(screen.getByRole("link", { name: "Archiviste" })).toHaveAttribute(
      "href",
      "/"
    );
    expect(screen.getByRole("link", { name: "Lacunes" })).toHaveAttribute(
      "href",
      "/lacunes"
    );
    expect(
      screen.getByRole("link", { name: "État & métriques" })
    ).toHaveAttribute("href", "/metriques");
  });

  it("closes the popover when the brand button is clicked again", () => {
    renderShell(ANONYMOUS);
    const brand = screen.getByRole("button", { name: /archiviste nocilia/i });
    fireEvent.click(brand);
    expect(screen.getByRole("link", { name: "Lacunes" })).toBeInTheDocument();
    fireEvent.click(brand);
    expect(
      screen.queryByRole("link", { name: "Lacunes" })
    ).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Dashboard link by tier
// ---------------------------------------------------------------------------

describe("SidebarShell — Dashboard link by tier", () => {
  it("shows the Dashboard link in the popover for the author tier", () => {
    renderShell(AUTHOR);
    fireEvent.click(screen.getByRole("button", { name: /archiviste nocilia/i }));
    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute(
      "href",
      "/dashboard"
    );
  });

  it("hides the Dashboard link for the member tier", () => {
    renderShell(MEMBER);
    fireEvent.click(screen.getByRole("button", { name: /archiviste nocilia/i }));
    expect(
      screen.queryByRole("link", { name: "Dashboard" })
    ).not.toBeInTheDocument();
  });

  it("hides the Dashboard link for the anonymous tier", () => {
    renderShell(ANONYMOUS);
    fireEvent.click(screen.getByRole("button", { name: /archiviste nocilia/i }));
    expect(
      screen.queryByRole("link", { name: "Dashboard" })
    ).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Account block by auth state
// ---------------------------------------------------------------------------

describe("SidebarShell — account block", () => {
  it("shows S'inscrire and Se connecter for anonymous", () => {
    renderShell(ANONYMOUS);
    expect(screen.getByRole("link", { name: /S.inscrire/i })).toHaveAttribute(
      "href",
      "/signup"
    );
    expect(screen.getByRole("link", { name: "Se connecter" })).toHaveAttribute(
      "href",
      "/login"
    );
    expect(
      screen.queryByRole("link", { name: "Se déconnecter" })
    ).not.toBeInTheDocument();
  });

  it("shows the email and Se déconnecter for a connected member", () => {
    renderShell(MEMBER);
    expect(screen.getByText("member@example.com")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Se déconnecter" })).toHaveAttribute(
      "href",
      "/logout"
    );
    expect(
      screen.queryByRole("link", { name: "Se connecter" })
    ).not.toBeInTheDocument();
  });

  it("shows Se déconnecter without crashing when a connected email is null", () => {
    renderShell({ tier: "member", email: null });
    expect(
      screen.getByRole("link", { name: "Se déconnecter" })
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// "Nouvelle conversation"
// ---------------------------------------------------------------------------

describe("SidebarShell — Nouvelle conversation", () => {
  it("is always visible", () => {
    renderShell(ANONYMOUS);
    expect(
      screen.getByRole("button", { name: /nouvelle conversation/i })
    ).toBeInTheDocument();
  });

  it("resets the thread via the registered chat handler on the chat page", () => {
    const onNew = vi.fn();
    renderShellWithChat(ANONYMOUS, <div>history</div>, onNew);
    fireEvent.click(
      screen.getByRole("button", { name: /nouvelle conversation/i })
    );
    expect(onNew).toHaveBeenCalledTimes(1);
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("navigates to / when no chat handler is registered (other pages)", () => {
    currentPathname = "/lacunes";
    renderShell(ANONYMOUS);
    fireEvent.click(
      screen.getByRole("button", { name: /nouvelle conversation/i })
    );
    expect(mockPush).toHaveBeenCalledWith("/");
  });
});

// ---------------------------------------------------------------------------
// Conversation history slot (chat page only)
// ---------------------------------------------------------------------------

describe("SidebarShell — conversation history slot", () => {
  it("renders the registered history element on the chat page", () => {
    renderShellWithChat(
      ANONYMOUS,
      <div data-testid="history-content">mon historique</div>,
      vi.fn()
    );
    expect(screen.getByTestId("history-content")).toBeInTheDocument();
  });

  it("renders no history when nothing is registered (other pages)", () => {
    renderShell(ANONYMOUS);
    expect(screen.queryByTestId("history-content")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Mobile drawer
// ---------------------------------------------------------------------------

describe("SidebarShell — mobile drawer", () => {
  it("exposes a hamburger button that opens the drawer", () => {
    renderShell(ANONYMOUS);
    const hamburger = screen.getByRole("button", { name: /ouvrir le menu/i });
    expect(hamburger).toBeInTheDocument();
    fireEvent.click(hamburger);
    expect(
      screen.getByRole("button", { name: /fermer le menu/i })
    ).toBeInTheDocument();
  });
});
