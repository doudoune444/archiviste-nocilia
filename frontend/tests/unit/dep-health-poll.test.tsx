// Unit tests for DepHealth polling behaviour — WEBOBS-002
//
// AC2: the island auto-refreshes ~every 60 s without a manual reload.
//
// Coverage strategy: jsdom + fake timers prove that:
//   (a) fetch is called once on mount (initial poll),
//   (b) fetch is called a second time after POLL_INTERVAL_MS elapses,
//   (c) unmounting before the interval fires does NOT trigger an additional fetch
//       (the interval is cleared on unmount).
//
// The AC2 claim in the e2e spec previously stated "verified by mock clock
// interception" — that was inaccurate; the e2e tests never advance time.
// THIS file is the actual proof of AC2.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  act,
  screen,
  within,
  fireEvent,
  cleanup,
} from "@testing-library/react";

// DepHealth is a "use client" component; jsdom environment handles that.
import { DepHealth } from "@/components/dep-health/DepHealth";

// Import the named constant so the test stays in sync if the value changes.
// Avoid duplicating the literal 60_000 here (clean-code.md: no magic constants).
import { POLL_INTERVAL_MS } from "@/components/dep-health/DepHealth";

const STATUS_OK = {
  status: "ok",
  dependencies: {
    postgres: { status: "ok", latency_ms: 3 },
    gcs: { status: "ok", latency_ms: 12 },
    workers: { status: "ok", latency_ms: 8 },
  },
  checked_at: "2026-06-19T10:00:00Z",
};

function makeOkFetch(): ReturnType<typeof vi.fn> {
  return vi.fn().mockResolvedValue(
    new Response(JSON.stringify(STATUS_OK), {
      status: 200,
      headers: { "content-type": "application/json" },
    })
  );
}

describe("DepHealth polling (AC2)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  // AC2 (a): fetch is called immediately on mount (initial poll fires synchronously).
  it("calls fetch once on initial mount", async () => {
    const mockFetch = makeOkFetch();
    vi.stubGlobal("fetch", mockFetch);

    await act(async () => {
      render(<DepHealth />);
    });

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/status");
  });

  // AC2 (b): fetch is called a second time after POLL_INTERVAL_MS elapses.
  it("calls fetch a second time after POLL_INTERVAL_MS", async () => {
    const mockFetch = makeOkFetch();
    vi.stubGlobal("fetch", mockFetch);

    await act(async () => {
      render(<DepHealth />);
    });

    // First poll happened on mount.
    expect(mockFetch).toHaveBeenCalledTimes(1);

    // Advance fake clock by exactly one interval and flush microtasks.
    await act(async () => {
      vi.advanceTimersByTime(POLL_INTERVAL_MS);
    });

    // The interval callback fired → a second fetch must have been issued.
    expect(mockFetch).toHaveBeenCalledTimes(2);
    expect(mockFetch).toHaveBeenNthCalledWith(2, "/api/v1/status");
  });

  // AC2 (c): unmounting before the interval fires clears the interval —
  // advancing the clock after unmount must NOT cause an extra fetch.
  it("does not fetch again after unmount (interval cleared on cleanup)", async () => {
    const mockFetch = makeOkFetch();
    vi.stubGlobal("fetch", mockFetch);

    let unmount!: () => void;
    await act(async () => {
      ({ unmount } = render(<DepHealth />));
    });

    expect(mockFetch).toHaveBeenCalledTimes(1);

    // Unmount the component — the cleanup function must call clearInterval.
    act(() => {
      unmount();
    });

    // Advance the clock past the interval boundary.
    await act(async () => {
      vi.advanceTimersByTime(POLL_INTERVAL_MS);
    });

    // Still only one call: the interval was cleared before it could fire.
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// #253: Workers third state — "En veille" (dormant) renders honestly:
//   - label "En veille" (not "Hors service");
//   - NOT styled as down/red (the down status text must be absent on that row);
//   - an accessible InfoTooltip explains the scale-to-zero behaviour;
//   - postgres/gcs keep their ok/down rendering (no regression).
// ---------------------------------------------------------------------------

const STATUS_DORMANT = {
  status: "ok",
  dependencies: {
    postgres: { status: "ok", latency_ms: 3 },
    gcs: { status: "ok", latency_ms: 12 },
    workers: { status: "dormant", latency_ms: 5 },
  },
  checked_at: "2026-06-19T10:00:00Z",
};

async function renderWith(body: unknown): Promise<void> {
  const mockFetch = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    })
  );
  vi.stubGlobal("fetch", mockFetch);
  await act(async () => {
    render(<DepHealth />);
  });
}

describe("DepHealth workers dormant state (#253)", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders 'En veille' for a dormant workers and never 'Hors service'", async () => {
    await renderWith(STATUS_DORMANT);

    expect(screen.getByText("En veille")).toBeInTheDocument();
    expect(screen.queryByText("Hors service")).not.toBeInTheDocument();
  });

  it("does not style dormant workers as down (workers row not red/down)", async () => {
    await renderWith(STATUS_DORMANT);

    const card = screen.getByRole("article", { name: "Dépendances" });
    // The accessible status for the Workers row must NOT say "hors service".
    expect(
      within(card).queryByLabelText(/Workers hors service/i)
    ).not.toBeInTheDocument();
    // It must announce the dormant state instead.
    expect(
      within(card).getByLabelText(/Workers en veille/i)
    ).toBeInTheDocument();
  });

  it("exposes an accessible info tooltip explaining the cold start", async () => {
    await renderWith(STATUS_DORMANT);

    const trigger = screen.getByRole("button", {
      name: /en veille|à froid|demande/i,
    });
    expect(trigger).toBeInTheDocument();

    // Explanation is hidden until activated, then revealed (InfoTooltip contract).
    expect(
      screen.queryByText(/s'active automatiquement à la demande, à froid/i)
    ).not.toBeInTheDocument();
    fireEvent.click(trigger);
    expect(
      screen.getByText(/s'active automatiquement à la demande, à froid/i)
    ).toBeInTheDocument();
  });

  it("keeps postgres/gcs ok and a down dep red without regression", async () => {
    await renderWith({
      status: "degraded",
      dependencies: {
        postgres: { status: "down", latency_ms: 0 },
        gcs: { status: "ok", latency_ms: 12 },
        workers: { status: "dormant", latency_ms: 5 },
      },
      checked_at: "2026-06-19T10:00:00Z",
    });

    const card = screen.getByRole("article", { name: "Dépendances" });
    expect(within(card).getByLabelText(/PostgreSQL hors service/i)).toBeInTheDocument();
    expect(within(card).getByLabelText(/GCS opérationnel/i)).toBeInTheDocument();
    expect(within(card).getByText("En veille")).toBeInTheDocument();
  });
});
