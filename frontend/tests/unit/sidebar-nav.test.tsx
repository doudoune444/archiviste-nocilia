// AC #245 — sidebar navigation popover + account block.
//
// The brand button opens a popover containing the navigation links. Links are
// NOT shown until the popover is opened. Dashboard appears only for authors.
// The account block shows signup/login when anonymous, email/logout when
// connected. Labels/routes are the clarified ones: Archiviste (/),
// Lacunes (/lacunes), État & métriques (/metriques).

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { NavPopover, AccountBlock } from "@/components/app-shell/SidebarNav";

vi.mock("@/components/app-shell/AppShell.module.css", () => ({
  default: new Proxy({}, { get: (_t, prop: string) => prop }),
}));

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
  }: {
    href: string;
    children: React.ReactNode;
  }) => <a href={href}>{children}</a>,
}));

describe("NavPopover (#245)", () => {
  it("hides navigation links until the brand popover is opened", () => {
    render(<NavPopover tier="member" />);
    expect(
      screen.queryByRole("link", { name: "Archiviste" })
    ).not.toBeInTheDocument();
  });

  it("opens the popover from the brand button and shows the nav links", () => {
    render(<NavPopover tier="member" />);
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

  it("shows the Dashboard link in the popover only for authors", () => {
    render(<NavPopover tier="author" />);
    fireEvent.click(screen.getByRole("button", { name: /archiviste nocilia/i }));
    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute(
      "href",
      "/dashboard"
    );
  });

  it("does not show the Dashboard link for non-authors", () => {
    render(<NavPopover tier="member" />);
    fireEvent.click(screen.getByRole("button", { name: /archiviste nocilia/i }));
    expect(
      screen.queryByRole("link", { name: "Dashboard" })
    ).not.toBeInTheDocument();
  });

  it("closes the popover when the brand button is toggled again", () => {
    render(<NavPopover tier="member" />);
    const brand = screen.getByRole("button", { name: /archiviste nocilia/i });
    fireEvent.click(brand);
    expect(screen.getByRole("link", { name: "Archiviste" })).toBeInTheDocument();
    fireEvent.click(brand);
    expect(
      screen.queryByRole("link", { name: "Archiviste" })
    ).not.toBeInTheDocument();
  });
});

describe("AccountBlock (#245)", () => {
  it("shows signup and login for anonymous", () => {
    render(<AccountBlock tier="anonymous" email={null} />);
    expect(
      screen.getByRole("link", { name: /s.inscrire/i })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Se connecter" })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Se déconnecter" })
    ).not.toBeInTheDocument();
  });

  it("shows email and logout for a connected member", () => {
    render(<AccountBlock tier="member" email="member@example.com" />);
    expect(screen.getByText("member@example.com")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Se déconnecter" })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Se connecter" })
    ).not.toBeInTheDocument();
  });
});
