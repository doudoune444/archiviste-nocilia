// AC: CHAT-005 — per-answer two-state contradiction report
//
// AC-1: each assistant answer carries a "Signaler une incohérence" action.
// AC-2: user can enter a claim and submit it to the report-contradiction endpoint.
// AC-3: clear two-state outcome: confirmed (recorded) OR not-confirmed.
//
// NOTE: gateway-backed assertions require a live gateway (GATEWAY_URL env set).
// Skipped in CI unless GATEWAY_URL is set — follows the pattern in chat.spec.ts.
//
// To run locally:
//   GATEWAY_URL=http://localhost:8080 npm run test:e2e -- tests/e2e/signal.spec.ts

import { test, expect } from "@playwright/test";

const hasLiveGateway = !!process.env["GATEWAY_URL"];

test.describe("signal form (CHAT-005)", () => {
  // AC-1: the signal trigger is present on each committed assistant answer.
  // This test exercises the UI shape and is gateway-dependent (needs an answer).
  test(
    "signal trigger appears under each assistant answer",
    async ({ page }) => {
      test.skip(!hasLiveGateway, "GATEWAY_URL not set — requires live gateway");

      await page.goto("/");
      await page.fill('textarea[name="question"]', "Qui est Nocilia ?");
      await page.click('button[type="submit"]');

      // Wait for the committed assistant answer to appear.
      await expect(
        page.locator('[data-testid="assistant-answer"]')
      ).not.toBeEmpty({ timeout: 30_000 });

      // AC-1: the signal trigger button is visible under the answer.
      await expect(
        page.getByRole("button", { name: /signaler une incohérence/i })
      ).toBeVisible();
    }
  );

  // AC-2 + AC-3: submitting a claim shows a two-state outcome.
  test(
    "submitting a claim shows the confirmed or not-confirmed outcome",
    async ({ page }) => {
      test.skip(!hasLiveGateway, "GATEWAY_URL not set — requires live gateway");

      await page.goto("/");
      await page.fill('textarea[name="question"]', "Qui est Nocilia ?");
      await page.click('button[type="submit"]');

      await expect(
        page.locator('[data-testid="assistant-answer"]')
      ).not.toBeEmpty({ timeout: 30_000 });

      // AC-2: open the signal form.
      await page
        .getByRole("button", { name: /signaler une incohérence/i })
        .click();

      // Enter a claim.
      await page.fill(
        'textarea[aria-label="Description de l\'incohérence"]',
        "Nocilia n'existe pas selon le livre III."
      );

      // AC-5: submit button is enabled when claim is non-empty.
      await expect(
        page.getByRole("button", { name: /envoyer le signalement/i })
      ).toBeEnabled();

      await page
        .getByRole("button", { name: /envoyer le signalement/i })
        .click();

      // AC-3: one of the two outcome states appears (confirmed or not-confirmed).
      await expect(
        page
          .locator('[data-testid="signal-outcome-confirmed"]')
          .or(page.locator('[data-testid="signal-outcome-not-confirmed"]'))
      ).toBeVisible({ timeout: 15_000 });
    }
  );

  // AC-5: submit is disabled when the claim textarea is empty (no gateway required).
  test("submit button is disabled when claim is empty", async ({ page }) => {
    test.skip(!hasLiveGateway, "GATEWAY_URL not set — requires live gateway");

    await page.goto("/");
    await page.fill('textarea[name="question"]', "Qui est Nocilia ?");
    await page.click('button[type="submit"]');

    await expect(
      page.locator('[data-testid="assistant-answer"]')
    ).not.toBeEmpty({ timeout: 30_000 });

    await page
      .getByRole("button", { name: /signaler une incohérence/i })
      .click();

    // AC-5: submit disabled when claim is empty.
    await expect(
      page.getByRole("button", { name: /envoyer le signalement/i })
    ).toBeDisabled();
  });
});
