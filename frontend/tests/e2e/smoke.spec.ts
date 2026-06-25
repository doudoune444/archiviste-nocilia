// AC: app loads, the sidebar app-shell renders (PLATFORM-001 + #248).
import { test, expect } from "@playwright/test";

test("la page d'accueil charge et affiche l'app-shell", async ({ page }) => {
  await page.goto("/");

  // #248: the persistent left sidebar replaces the top nav bar.
  await expect(page.locator("aside")).toBeVisible();
  await expect(
    page.getByRole("button", { name: /l'archiviste/i })
  ).toBeVisible();

  // The chat surface renders directly at the root.
  await expect(page.getByRole("textbox", { name: /question/i })).toBeVisible();

  // #248: the global footer is removed.
  await expect(page.locator("footer")).toHaveCount(0);
});
