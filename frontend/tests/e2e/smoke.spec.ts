// AC: app loads, layout renders (PLATFORM-001 — Playwright smoke)
import { test, expect } from "@playwright/test";

test("la page d'accueil charge et affiche le layout", async ({ page }) => {
  await page.goto("/");

  // Layout shell is present
  await expect(page.locator("nav")).toBeVisible();
  await expect(page.getByText("Archiviste Nocilia").first()).toBeVisible();

  // #247: the chat surface is the landing page — the question form is present
  await expect(
    page.getByRole("textbox", { name: /votre question/i })
  ).toBeVisible();

  // Footer is present
  await expect(page.locator("footer")).toBeVisible();
});
