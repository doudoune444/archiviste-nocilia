// AC: app loads, layout renders (PLATFORM-001 — Playwright smoke)
import { test, expect } from "@playwright/test";

test("la page d'accueil charge et affiche le layout", async ({ page }) => {
  await page.goto("/");

  // Layout shell is present (the primary nav — a second <nav> is the history sidebar)
  await expect(
    page.getByRole("navigation", { name: /navigation principale/i })
  ).toBeVisible();
  await expect(page.getByText("Archiviste Nocilia").first()).toBeVisible();

  // Home page content renders
  await expect(
    page.getByRole("heading", { name: /Bienvenue aux archives de Nocilia/i })
  ).toBeVisible();

  // #249: the chat surface is full-screen with no footer
  await expect(page.locator("footer")).toHaveCount(0);
});
