// Unit tests for the DepHealth Workers tri-state rendering — issue #253 (Lot 2).
//
// Seam 3: the Dependencies card must render the third Workers state honestly.
// Behaviour verified through the public render output (integration-style), never
// internal state or CSS class names. Prior art: tests/unit/dep-health-poll.test.tsx.
//
// Acceptance criteria covered:
//   - Workers "dormant" renders the short label "En veille" (not "Hors service")
//   - the dormant label is NOT styled/labelled as the red down state (US-3)
//   - an info tooltip trigger is present and accessible (US-4, US-10, US-11)
//   - "ok" / "down" Workers states still render "Opérationnel" / "Hors service"

import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup, within } from "@testing-library/react";
import { DepHealth } from "@/components/dep-health/DepHealth";

// jsdom cannot process real CSS modules; stub both modules to identity proxies.
vi.mock("@/components/dep-health/DepHealth.module.css", () => ({
  default: new Proxy({}, { get: (_t, prop: string) => prop }),
}));
vi.mock("@/components/info-tooltip/InfoTooltip.module.css", () => ({
  default: new Proxy({}, { get: (_t, prop: string) => prop }),
}));

function statusBody(workersStatus: string) {
  return {
    status: "ok",
    dependencies: {
      postgres: { status: "ok", latency_ms: 3 },
      gcs: { status: "ok", latency_ms: 12 },
      workers: { status: workersStatus, latency_ms: 8 },
    },
    checked_at: "2026-06-23T10:00:00Z",
  };
}

function stubFetch(workersStatus: string) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify(statusBody(workersStatus)), {
        status: 200,
        headers: { "content-type": "application/json" },
      })
    )
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

describe("DepHealth Workers tri-state (#253)", () => {
  it("renders 'En veille' for dormant workers, never 'Hors service'", async () => {
    stubFetch("dormant");
    render(<DepHealth />);

    expect(await screen.findByText("En veille")).toBeInTheDocument();
    // US-3: dormant must not surface the down label anywhere.
    expect(screen.queryByText("Hors service")).not.toBeInTheDocument();
  });

  it("labels the dormant Workers state accessibly (not as down)", async () => {
    stubFetch("dormant");
    render(<DepHealth />);

    // US-11: an accessible label conveys the dormant state, never "hors service".
    expect(await screen.findByLabelText("Workers en veille")).toBeInTheDocument();
    expect(
      screen.queryByLabelText(/workers hors service/i)
    ).not.toBeInTheDocument();
  });

  it("exposes an accessible info trigger next to the dormant state", async () => {
    stubFetch("dormant");
    render(<DepHealth />);

    await screen.findByText("En veille");
    // US-4 / US-10 / US-11: a focusable, labelled info trigger (button) is present.
    const trigger = screen.getByRole("button", { name: /en veille/i });
    expect(trigger).toBeInTheDocument();

    // US-10: tapping/clicking it reveals the cold-start explanation as a tooltip.
    fireEvent.click(trigger);
    const tooltip = await screen.findByRole("tooltip");
    expect(within(tooltip).getByText(/à froid/i)).toBeInTheDocument();
  });

  it("renders the scale-to-zero hint beneath the dependency list (#350)", async () => {
    stubFetch("dormant");
    render(<DepHealth />);

    await screen.findByText("En veille");
    // #350: verbatim hint from the v03 mockup (« scale-to-zero » is emphasised,
    // so match the whole text content across the inline <b>).
    const hint = screen.getByText((_content, element) => {
      return (
        element?.textContent ===
        "Workers en scale-to-zero : démarrage à froid à la demande."
      );
    });
    expect(hint).toBeInTheDocument();
  });

  it("still renders 'Opérationnel' when workers is ok (non-regression)", async () => {
    stubFetch("ok");
    render(<DepHealth />);

    // Three deps, all ok → three "Opérationnel" labels.
    const healthy = await screen.findAllByText("Opérationnel");
    expect(healthy.length).toBe(3);
    expect(screen.queryByText("En veille")).not.toBeInTheDocument();
  });

  it("still renders 'Hors service' when workers is down (non-regression)", async () => {
    stubFetch("down");
    render(<DepHealth />);

    expect(await screen.findByText("Hors service")).toBeInTheDocument();
    expect(screen.queryByText("En veille")).not.toBeInTheDocument();
  });
});
