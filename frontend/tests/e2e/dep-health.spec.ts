// AC1: postgres, gcs, workers each render with an unambiguous healthy/down status.
// AC2: auto-refresh ~every 60 s — proved by fake-timer unit test in
//      tests/unit/dep-health-poll.test.tsx (vi.useFakeTimers + vi.advanceTimersByTime).
//      This e2e file does NOT advance time and does NOT verify AC2.
// AC3: polling goes through /api/v1/status (bff-proxy), not directly to gateway.
// AC4: the island fits the existing signal-card layout.
//
// NOTE: a live Next.js server is required. Gateway calls are intercepted via
// Playwright route mocking — no real gateway needed.

import { test, expect } from "@playwright/test";

const STATUS_OK = {
  status: "ok",
  dependencies: {
    postgres: { status: "ok", latency_ms: 3 },
    gcs: { status: "ok", latency_ms: 12 },
    workers: { status: "ok", latency_ms: 8 },
  },
  checked_at: "2026-06-19T10:00:00Z",
};

const STATUS_DEGRADED = {
  status: "degraded",
  dependencies: {
    postgres: { status: "down", latency_ms: 0 },
    gcs: { status: "ok", latency_ms: 12 },
    workers: { status: "ok", latency_ms: 8 },
  },
  checked_at: "2026-06-19T10:00:00Z",
};

// AC3: intercept the same-origin bff-proxy route, not the gateway directly.
test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/status", (route) => {
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(STATUS_OK),
    });
  });
});

test("AC1: dépendances affiche les trois services comme opérationnels", async ({
  page,
}) => {
  await page.goto("/metriques");

  const card = page.getByRole("article", { name: "Dépendances" });
  await expect(card).toBeVisible();

  // AC1: each of the three deps must be visible and clearly healthy
  await expect(card.getByText("PostgreSQL")).toBeVisible();
  await expect(card.getByText("GCS")).toBeVisible();
  await expect(card.getByText("Workers")).toBeVisible();

  // All three are healthy in the mock — each row shows "Opérationnel"
  const statusLabels = card.getByText("Opérationnel");
  await expect(statusLabels).toHaveCount(3);
});

test("AC1: dépendances affiche postgres hors service sans ambiguïté", async ({
  page,
}) => {
  // Override with degraded payload for this test only
  await page.route("**/api/v1/status", (route) => {
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(STATUS_DEGRADED),
    });
  });

  await page.goto("/metriques");

  const card = page.getByRole("article", { name: "Dépendances" });
  await expect(card).toBeVisible();

  // postgres must be clearly down
  await expect(card.getByText("Hors service")).toBeVisible();

  // gcs and workers remain healthy
  const statusLabels = card.getByText("Opérationnel");
  await expect(statusLabels).toHaveCount(2);
});

// #253: Workers scale-to-zero (Cloud Run Ready=True) → "En veille", neutral, never red.
const STATUS_WORKERS_DORMANT = {
  status: "ok",
  dependencies: {
    postgres: { status: "ok", latency_ms: 3 },
    gcs: { status: "ok", latency_ms: 12 },
    workers: { status: "dormant", latency_ms: 5 },
  },
  checked_at: "2026-06-19T10:00:00Z",
};

test("#253: Workers en veille affiché comme état nominal, pas une panne", async ({
  page,
}) => {
  await page.route("**/api/v1/status", (route) => {
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(STATUS_WORKERS_DORMANT),
    });
  });

  await page.goto("/metriques");

  const card = page.getByRole("article", { name: "Dépendances" });
  await expect(card).toBeVisible();

  // US-2/US-3: "En veille" label present, never "Hors service".
  await expect(card.getByText("En veille")).toBeVisible();
  await expect(card.getByText("Hors service")).toHaveCount(0);

  // #350: the verbatim scale-to-zero hint is shown under the row.
  await expect(
    card.getByText(
      "Workers en scale-to-zero : démarrage à froid à la demande."
    )
  ).toBeVisible();

  // US-4/US-10: an accessible info trigger explains the cold start on demand.
  const trigger = card.getByRole("button", { name: /en veille/i });
  await expect(trigger).toBeVisible();
  await trigger.click();
  await expect(page.getByRole("tooltip")).toContainText("à froid");
});

test("AC1: état d'erreur rendu sans ambiguïté quand /api/v1/status échoue", async ({
  page,
}) => {
  await page.route("**/api/v1/status", (route) => {
    return route.fulfill({ status: 503, body: "" });
  });

  await page.goto("/metriques");

  const card = page.getByRole("article", { name: "Dépendances" });
  await expect(card).toBeVisible();
  await expect(
    card.getByText(/Impossible de vérifier l['']état des dépendances/)
  ).toBeVisible();
});

test("AC4: la carte dépendances s'intègre dans le bandeau métriques", async ({
  page,
}) => {
  await page.goto("/metriques");

  // Page heading is present (renamed shell, #347)
  await expect(
    page.getByRole("heading", { name: "État et métriques" })
  ).toBeVisible();

  // The dep-health card is in the band alongside the other signal cards
  const card = page.getByRole("article", { name: "Dépendances" });
  await expect(card).toBeVisible();

  // Other existing cards still render (AC4: no regression). The Conversations
  // card is the renamed former « Statistiques » card (#350).
  await expect(
    page.getByRole("article", { name: "Conversations" })
  ).toBeVisible();
});
