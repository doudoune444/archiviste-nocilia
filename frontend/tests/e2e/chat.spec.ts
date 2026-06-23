// AC #245 — chat at the root, Gemini/Mistral style.
//
// `/` is the chat directly (no CTA click). The welcome state shows a short
// welcome heading, the centered input and suggestion chips. Sending a question
// (or clicking a chip) switches to the conversation state with the input
// anchored at the bottom. The popover navigates to Lacunes / État & métriques.
//
// Gateway-backed streaming assertions are skipped without GATEWAY_URL — mirrors
// the auth.spec.ts pattern.

import { test, expect } from "@playwright/test";

const hasLiveGateway = !!process.env["GATEWAY_URL"];

test.describe("chat at root (#245)", () => {
  // AC: `/` renders the chat input directly, with no "Interroger l'archiviste" CTA.
  test("root renders the chat input directly, no CTA", async ({ page }) => {
    await page.goto("/");
    await expect(
      page.getByRole("textbox", { name: /votre question/i })
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: /interroger l'archiviste/i })
    ).toHaveCount(0);
  });

  // AC: welcome state shows the short welcome heading + chips.
  test("welcome state shows the welcome heading and suggestion chips", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: /bienvenue aux archives de nocilia/i })
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Qui est Blowen ?" })
    ).toBeVisible();
  });

  // AC: the brand popover navigates to the clarified routes.
  test("brand popover navigates to Lacunes and État & métriques", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByRole("button", { name: /archiviste nocilia/i }).click();

    await page.getByRole("link", { name: "Lacunes" }).click();
    await expect(page).toHaveURL(/\/lacunes$/);

    await page.goto("/");
    await page.getByRole("button", { name: /archiviste nocilia/i }).click();
    await page.getByRole("link", { name: "État & métriques" }).click();
    await expect(page).toHaveURL(/\/metriques$/);
  });

  // AC-smoke: sending a question streams a visible answer and anchors the input.
  test("streaming answer becomes visible after submitting a question", async ({
    page,
  }) => {
    test.skip(!hasLiveGateway, "GATEWAY_URL not set — requires live gateway");

    await page.goto("/");

    const question = "Qui est Nocilia ?";
    await page.fill('textarea[name="question"]', question);
    await page.click('button[type="submit"]');

    await expect(page.getByText(question)).toBeVisible();
    await expect(
      page.locator('[data-testid="assistant-answer"]')
    ).not.toBeEmpty({ timeout: 30_000 });
  });
});
