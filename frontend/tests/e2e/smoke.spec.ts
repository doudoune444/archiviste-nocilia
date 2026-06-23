// AC: app loads, layout renders (PLATFORM-001 — Playwright smoke)
import { test, expect } from "@playwright/test";

test("la page d'accueil charge et affiche le layout", async ({ page }) => {
  await page.goto("/");

  // Layout shell is present
  await expect(page.locator("nav")).toBeVisible();
  await expect(page.getByText("Archiviste Nocilia").first()).toBeVisible();

  // Home page content renders
  await expect(
    page.getByRole("heading", { name: /Bienvenue aux archives de Nocilia/i })
  ).toBeVisible();
});
