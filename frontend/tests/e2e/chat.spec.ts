// AC: CHAT-002 — streaming chat surface smoke
//
// AC-smoke: sending a question streams a visible answer to the chat surface.
//
// NOTE: requires a live gateway (GATEWAY_URL env set and responding).
// Without a live gateway the BFF API route cannot relay the SSE stream.
// Skipped in CI unless GATEWAY_URL is set — mirrors the auth.spec.ts pattern.
//
// To run locally:
//   GATEWAY_URL=http://localhost:8080 npm run test:e2e -- tests/e2e/chat.spec.ts

import { test, expect } from "@playwright/test";

const hasLiveGateway = !!process.env["GATEWAY_URL"];

test.describe("chat surface (CHAT-002)", () => {
  // AC: /chat renders a text input and a send button
  test("chat page renders the question form", async ({ page }) => {
    await page.goto("/chat");

    await expect(
      page.getByRole("textbox", { name: /question/i })
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: /envoyer/i })
    ).toBeVisible();
  });

  // AC-smoke: sending a question streams a visible answer
  test("streaming answer becomes visible after submitting a question", async ({
    page,
  }) => {
    test.skip(!hasLiveGateway, "GATEWAY_URL not set — requires live gateway");

    await page.goto("/chat");

    const question = "Qui est Nocilia ?";
    await page.fill('textarea[name="question"]', question);
    await page.click('button[type="submit"]');

    // AC: the user's message is echoed immediately (optimistic)
    await expect(page.getByText(question)).toBeVisible();

    // AC: an answer eventually appears (streaming completes).
    // The committed assistant bubble uses data-testid="assistant-answer";
    // the live streaming placeholder uses data-testid="streaming-answer".
    // We wait for the committed bubble — it appears once streaming ends.
    await expect(page.locator('[data-testid="assistant-answer"]')).not.toBeEmpty({
      timeout: 30_000,
    });
  });
});
