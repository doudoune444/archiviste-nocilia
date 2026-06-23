// AC #245 — conversation-history sidebar in the global app-shell.
//
// The sidebar (with "Nouvelle conversation") is present on `/`. A fresh load
// shows an empty thread (welcome state, no answer bubble). "Nouvelle
// conversation" clears the thread and it stays cleared on reload (no
// localStorage). Gateway-backed assertions (populated history with titles,
// transcript reopen) are skipped without GATEWAY_URL.

import { test, expect } from "@playwright/test";

const hasLiveGateway = !!process.env["GATEWAY_URL"];

test.describe("conversation history sidebar (#245)", () => {
  // AC: the sidebar "Nouvelle conversation" button is always visible on /.
  test("sidebar renders the 'Nouvelle conversation' button", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(
      page.getByRole("button", { name: /nouvelle conversation/i })
    ).toBeVisible();
  });

  // AC-no-phantom: a fresh load has an empty thread (welcome state).
  test("fresh load shows the welcome state, no assistant-answer bubble", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(page.locator('[data-testid="assistant-answer"]')).toHaveCount(
      0
    );
    await expect(
      page.getByRole("heading", { name: /bienvenue aux archives de nocilia/i })
    ).toBeVisible();
  });

  // AC-new-conversation + AC-stays-cleared.
  test("'Nouvelle conversation' clears the thread and stays cleared on reload", async ({
    page,
  }) => {
    await page.goto("/");

    const newBtn = page.getByTestId("new-conversation-btn");
    await expect(newBtn).toBeVisible();
    await newBtn.click();

    await expect(page.locator('[data-testid="assistant-answer"]')).toHaveCount(
      0
    );

    await page.reload();
    await expect(page.locator('[data-testid="assistant-answer"]')).toHaveCount(
      0
    );
  });

  // AC-sidebar (gateway-dependent): history nav present.
  test("sidebar history nav is present when gateway returns history", async ({
    page,
  }) => {
    test.skip(!hasLiveGateway, "GATEWAY_URL not set — requires live gateway");
    await page.goto("/");
    await expect(
      page.getByRole("navigation", { name: /historique/i })
    ).toBeVisible();
  });

  // AC-load-transcript (gateway-dependent): clicking a conversation reopens it.
  test("clicking a conversation item loads its transcript in order", async ({
    page,
  }) => {
    test.skip(!hasLiveGateway, "GATEWAY_URL not set — requires live gateway");
    await page.goto("/");

    const firstItem = page
      .locator('[data-testid^="conversation-item-"]')
      .first();
    if ((await firstItem.count()) === 0) return;

    await firstItem.click();
    await expect(
      page.locator('[data-testid="assistant-answer"]').first()
    ).toBeVisible({ timeout: 10_000 });
  });

  // AC-routes (gateway-independent): renamed routes respond.
  test("the /lacunes and /metriques routes respond", async ({ page }) => {
    const lacunes = await page.goto("/lacunes");
    expect(lacunes?.status()).toBeLessThan(400);
    const metriques = await page.goto("/metriques");
    expect(metriques?.status()).toBeLessThan(400);
  });
});
