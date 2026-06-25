// AC #248 — sidebar brand popover navigation.
//
// The brand button opens a popover whose links navigate to the renamed routes
// Lacunes (/lacunes) and État & métriques (/metriques).

import { test, expect } from "@playwright/test";

test.describe("sidebar brand popover navigation (#248)", () => {
  test("navigates to Lacunes from the brand popover", async ({ page }) => {
    await page.goto("/");

    await page.getByRole("button", { name: /l'archiviste/i }).click();
    await page.getByRole("link", { name: "Lacunes" }).click();

    await expect(page).toHaveURL(/\/lacunes$/);
  });

  test("navigates to État & métriques from the brand popover", async ({
    page,
  }) => {
    await page.goto("/");

    await page.getByRole("button", { name: /l'archiviste/i }).click();
    await page.getByRole("link", { name: "État & métriques" }).click();

    await expect(page).toHaveURL(/\/metriques$/);
  });
});
