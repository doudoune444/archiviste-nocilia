// AC: CHAT-002 streaming smoke + #249 welcome state / chips / centered→bottom transition.
//
// Welcome-state assertions (title, centered input, chips) run without a gateway.
// Stream-dependent assertions (chip-send round-trip, centered→bottom transition)
// require a live gateway and are skipped unless GATEWAY_URL is set — mirrors the
// auth.spec.ts pattern so CI without a gateway stays green.
//
// To run the gateway-backed cases locally:
//   GATEWAY_URL=http://localhost:8080 npm run test:e2e -- tests/e2e/chat.spec.ts

import { test, expect } from "@playwright/test";

const hasLiveGateway = !!process.env["GATEWAY_URL"];

test.describe("chat surface (CHAT-002)", () => {
  // AC: the chat renders a text input and a send button
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
    await expect(page.locator('[data-testid="assistant-answer"]')).not.toBeEmpty({
      timeout: 30_000,
    });
  });
});

test.describe("chat welcome state (#249)", () => {
  // AC: empty thread shows the welcome title, a centered input, and four chips.
  test("welcome state shows title, centered input and four suggestion chips", async ({
    page,
  }) => {
    await page.goto("/chat");

    await expect(
      page.getByRole("heading", { name: /Bienvenue aux archives de Nocilia/i })
    ).toBeVisible();

    await expect(
      page.getByRole("textbox", { name: /question/i })
    ).toBeVisible();

    const surface = page.locator('[data-state="welcome"]');
    await expect(surface).toBeVisible();

    for (const label of [
      "Qui est Blowen ?",
      "Qu'est-ce que le Cérafon ?",
      "Qui a élu domicile dans les ruines de Periste ?",
      "Combien font 2+2 ?",
    ]) {
      await expect(page.getByRole("button", { name: label })).toBeVisible();
    }
  });

  // AC: clicking a chip sends that exact question and switches to conversation
  // state with the input anchored to the bottom and the thread above.
  test("clicking a chip sends the question and transitions centered → bottom", async ({
    page,
  }) => {
    test.skip(!hasLiveGateway, "GATEWAY_URL not set — requires live gateway");

    await page.goto("/chat");

    const chip = "Combien font 2+2 ?";
    await page.getByRole("button", { name: chip }).click();

    // AC: the exact chip text is echoed as the user message.
    await expect(page.getByText(chip)).toBeVisible();

    // AC: the surface is now in conversation state (welcome layout is gone).
    await expect(page.locator('[data-state="conversation"]')).toBeVisible();
    await expect(page.locator('[data-state="welcome"]')).toHaveCount(0);

    // AC: a streamed answer eventually appears in the thread above the input.
    await expect(page.locator('[data-testid="assistant-answer"]')).not.toBeEmpty({
      timeout: 30_000,
    });
  });
});
