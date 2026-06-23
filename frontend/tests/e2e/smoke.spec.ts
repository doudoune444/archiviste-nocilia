// AC: app loads, layout renders (PLATFORM-001 — Playwright smoke)
import { test, expect } from "@playwright/test";

test("la page d'accueil charge et affiche le layout", async ({ page }) => {
  await page.goto("/");

  // Layout shell is present
  await expect(
    page.getByRole("navigation", { name: "Navigation principale" })
  ).toBeVisible();
  await expect(page.getByText("Archiviste Nocilia").first()).toBeVisible();

  // Root is the chat surface (#247: chat à la racine)
  await expect(
    page.getByRole("heading", { name: /Chat — Archives de Nocilia/i })
  ).toBeVisible();

  // Footer is present
  await expect(page.locator("footer")).toBeVisible();
});
