// AC: PLATFORM-003 — auth-aware nav
// AC1: Static view links present for ALL tiers (/, /lacunes, /metriques) (#247)
// AC2: anonymous → "Se connecter" / "S'inscrire", no email, no dashboard
// AC3: member → email + "Se déconnecter", no dashboard link
// AC4: author → email + "Se déconnecter" + "/dashboard" link
// AC5: malformed/failed /v1/me degrades to anonymous variant (fail-soft)

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

// Mock next/headers — cookies() and headers() are not available in test env.
vi.mock("next/headers", () => ({
  cookies: vi.fn().mockResolvedValue({
    toString: () => "",
  }),
  headers: vi.fn().mockResolvedValue({
    get: () => null,
  }),
}));

// Mock bff-proxy — controlled per test via mockForward.
const mockForward = vi.fn<(req: Request, path: string) => Promise<Response>>();
vi.mock("@/lib/bff-proxy", () => ({
  forward: (req: Request, path: string) => mockForward(req, path),
}));

// Mock next/link — render as plain <a> so assertions work in jsdom.
vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    className,
  }: {
    href: string;
    children: React.ReactNode;
    className?: string;
  }) => (
    <a href={href} className={className}>
      {children}
    </a>
  ),
}));

import AuthAwareNav from "@/components/auth-aware-nav/AuthAwareNav";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeGatewayResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function renderNav() {
  // AuthAwareNav is an async server component; await it before rendering.
  const element = await AuthAwareNav();
  render(element as React.ReactElement);
}

// ---------------------------------------------------------------------------
// AC1: Static view links — present for ALL tiers
// ---------------------------------------------------------------------------

describe("AuthAwareNav — static view links (AC1)", () => {
  beforeEach(() => {
    mockForward.mockResolvedValue(
      makeGatewayResponse({ tier: "anonymous", email: null })
    );
  });

  it("renders the Archiviste link to /", async () => {
    // AC1 (#247): chat link present for all tiers, labelled Archiviste
    await renderNav();
    const link = screen.getByRole("link", { name: "Archiviste" });
    expect(link).toHaveAttribute("href", "/");
  });

  it("renders the Lacunes link to /lacunes", async () => {
    // AC1 (#247): board link renamed to Lacunes / /lacunes
    await renderNav();
    const link = screen.getByRole("link", { name: "Lacunes" });
    expect(link).toHaveAttribute("href", "/lacunes");
  });

  it("renders the État & métriques link to /metriques", async () => {
    // AC1 (#247): observability link renamed to État & métriques / /metriques
    await renderNav();
    const link = screen.getByRole("link", { name: "État & métriques" });
    expect(link).toHaveAttribute("href", "/metriques");
  });
});

// ---------------------------------------------------------------------------
// AC2: Anonymous tier — auth cluster
// ---------------------------------------------------------------------------

describe("AuthAwareNav — anonymous tier (AC2)", () => {
  beforeEach(() => {
    mockForward.mockResolvedValue(
      makeGatewayResponse({ tier: "anonymous", email: null })
    );
  });

  it("renders 'Se connecter' link for anonymous", async () => {
    // AC2: anonymous → login link present
    await renderNav();
    expect(
      screen.getByRole("link", { name: "Se connecter" })
    ).toBeInTheDocument();
  });

  it("renders 'S'inscrire' link for anonymous", async () => {
    // AC2: anonymous → signup link present
    await renderNav();
    expect(
      screen.getByRole("link", { name: /S.inscrire/i })
    ).toBeInTheDocument();
  });

  it("does not render 'Se déconnecter' for anonymous", async () => {
    // AC2: anonymous → no logout
    await renderNav();
    expect(
      screen.queryByRole("link", { name: "Se déconnecter" })
    ).not.toBeInTheDocument();
  });

  it("does not render the dashboard link for anonymous", async () => {
    // AC2: anonymous → no dashboard
    await renderNav();
    expect(
      screen.queryByRole("link", { name: "Dashboard" })
    ).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AC3: Member tier — auth cluster
// ---------------------------------------------------------------------------

describe("AuthAwareNav — member tier (AC3)", () => {
  beforeEach(() => {
    mockForward.mockResolvedValue(
      makeGatewayResponse({ tier: "member", email: "member@example.com" })
    );
  });

  it("renders the email for member", async () => {
    // AC3: member → email displayed
    await renderNav();
    expect(screen.getByText("member@example.com")).toBeInTheDocument();
  });

  it("renders 'Se déconnecter' for member", async () => {
    // AC3: member → logout present
    await renderNav();
    expect(
      screen.getByRole("link", { name: "Se déconnecter" })
    ).toBeInTheDocument();
  });

  it("does not render the dashboard link for member", async () => {
    // AC3: member → no dashboard (author-only)
    await renderNav();
    expect(
      screen.queryByRole("link", { name: "Dashboard" })
    ).not.toBeInTheDocument();
  });

  it("does not render login/signup links for member", async () => {
    // AC3: member → no anonymous auth cluster
    await renderNav();
    expect(
      screen.queryByRole("link", { name: "Se connecter" })
    ).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AC3 edge: member with email=null (fail-soft from gateway)
// ---------------------------------------------------------------------------

describe("AuthAwareNav — member with missing email (AC3 edge)", () => {
  beforeEach(() => {
    mockForward.mockResolvedValue(
      makeGatewayResponse({ tier: "member", email: null })
    );
  });

  it("renders 'Se déconnecter' without crashing when email is null", async () => {
    // AC3 edge: email can be null if gateway lookup failed; must not crash
    await renderNav();
    expect(
      screen.getByRole("link", { name: "Se déconnecter" })
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AC4: Author tier — auth cluster
// ---------------------------------------------------------------------------

describe("AuthAwareNav — author tier (AC4)", () => {
  beforeEach(() => {
    mockForward.mockResolvedValue(
      makeGatewayResponse({ tier: "author", email: "author@example.com" })
    );
  });

  it("renders the dashboard link for author", async () => {
    // AC4: author → dashboard link present
    await renderNav();
    const link = screen.getByRole("link", { name: "Dashboard" });
    expect(link).toHaveAttribute("href", "/dashboard");
  });

  it("renders the email for author", async () => {
    // AC4: author → email displayed
    await renderNav();
    expect(screen.getByText("author@example.com")).toBeInTheDocument();
  });

  it("renders 'Se déconnecter' for author", async () => {
    // AC4: author → logout present
    await renderNav();
    expect(
      screen.getByRole("link", { name: "Se déconnecter" })
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AC5: Fail-soft — degraded anonymous on bad /v1/me
// ---------------------------------------------------------------------------

describe("AuthAwareNav — fail-soft degradation (AC5)", () => {
  it("renders anonymous variant when forward() throws", async () => {
    // AC5: network error → anonymous variant (no crash, no logout link)
    mockForward.mockRejectedValue(new Error("network error"));
    await renderNav();
    expect(
      screen.getByRole("link", { name: "Se connecter" })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Se déconnecter" })
    ).not.toBeInTheDocument();
  });

  it("renders anonymous variant when /v1/me returns 500", async () => {
    // AC5: non-OK status → anonymous variant
    mockForward.mockResolvedValue(new Response(null, { status: 500 }));
    await renderNav();
    expect(
      screen.getByRole("link", { name: "Se connecter" })
    ).toBeInTheDocument();
  });

  it("renders anonymous variant when body is malformed JSON", async () => {
    // AC5: JSON parse failure → anonymous variant
    mockForward.mockResolvedValue(
      new Response("not-json", {
        status: 200,
        headers: { "content-type": "application/json" },
      })
    );
    await renderNav();
    expect(
      screen.getByRole("link", { name: "Se connecter" })
    ).toBeInTheDocument();
  });

  it("renders anonymous variant when tier field is unknown", async () => {
    // AC5: invalid tier value → isMeResponse guard rejects → anonymous
    mockForward.mockResolvedValue(
      makeGatewayResponse({ tier: "superadmin", email: null })
    );
    await renderNav();
    expect(
      screen.getByRole("link", { name: "Se connecter" })
    ).toBeInTheDocument();
  });

  it("renders anonymous variant when response body is not an object", async () => {
    // AC5: non-object body → isMeResponse guard rejects → anonymous
    mockForward.mockResolvedValue(makeGatewayResponse("unexpected string"));
    await renderNav();
    expect(
      screen.getByRole("link", { name: "Se connecter" })
    ).toBeInTheDocument();
  });
});
