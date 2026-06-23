// AC: CHAT-002 streaming smoke + #249 chat redesign (welcome → conversation).
//
// The CHAT-002 streaming smoke needs a live gateway (skipped when GATEWAY_URL
// is unset). The #249 tests stub the SSE stream with page.route so they run
// fully offline — they assert UI behavior (chips, centered→bottom transition),
// not the gateway.
//
// To run the live smoke locally:
//   GATEWAY_URL=http://localhost:8080 npm run test:e2e -- tests/e2e/chat.spec.ts

import { test, expect } from "@playwright/test";

const hasLiveGateway = !!process.env["GATEWAY_URL"];

/** Body of a minimal successful SSE stream: meta → one token → done. */
const SSE_STREAM_BODY = [
  'event: meta\ndata: {"mode":"canon","conversation_id":"c1","request_id":"r1"}\n\n',
  'event: token\ndata: {"text":"Réponse de test."}\n\n',
  'event: done\ndata: {"citations":[],"usage":{},"retrieve_ms":0,"llm_ms":0}\n\n',
].join("");

/** Stubs the BFF chat-stream route with a canned SSE response (offline). */
async function stubChatStream(page: import("@playwright/test").Page): Promise<void> {
  await page.route("**/api/v1/chat/stream", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: SSE_STREAM_BODY,
    })
  );
  await page.route("**/api/v1/conversations", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ conversations: [] }),
    })
  );
}

test.describe("chat surface (CHAT-002)", () => {
  // AC: the root renders a text input and a send button
  test("chat page renders the question form", async ({ page }) => {
    await page.goto("/");

    await expect(page.getByRole("textbox", { name: /question/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /envoyer/i })).toBeVisible();
  });

  // AC-smoke: sending a question streams a visible answer (live gateway)
  test("streaming answer becomes visible after submitting a question", async ({
    page,
  }) => {
    test.skip(!hasLiveGateway, "GATEWAY_URL not set — requires live gateway");

    await page.goto("/");

    const question = "Qui est Nocilia ?";
    await page.fill('textarea[name="question"]', question);
    await page.click('button[type="submit"]');

    await expect(page.getByText(question)).toBeVisible();
    await expect(page.locator('[data-testid="assistant-answer"]')).not.toBeEmpty({
      timeout: 30_000,
    });
  });
});

test.describe("chat redesign — welcome state (#249)", () => {
  // AC: empty thread shows the welcome title, a centered input, and four chips
  test("empty thread shows the welcome title and four suggestion chips", async ({
    page,
  }) => {
    await page.goto("/");

    await expect(
      page.getByRole("heading", { name: /Bienvenue aux archives de Nocilia/i })
    ).toBeVisible();
    await expect(page.getByTestId("welcome-state")).toBeVisible();
    await expect(page.getByTestId("suggestion-chip")).toHaveCount(4);
    await expect(page.getByRole("button", { name: /Qui est Blowen/i })).toBeVisible();
  });

  // AC: clicking a chip sends that exact question and switches to conversation state
  test("clicking a chip sends the question and switches to conversation state", async ({
    page,
  }) => {
    await stubChatStream(page);
    await page.goto("/");

    await page.getByRole("button", { name: /Qui est Blowen/i }).click();

    // Optimistic echo of the chip text appears in the thread.
    await expect(page.getByText("Qui est Blowen ?")).toBeVisible();
    // The welcome state is replaced by the conversation state.
    await expect(page.getByTestId("welcome-state")).toHaveCount(0);
    await expect(page.getByTestId("conversation-state")).toBeVisible();
    // The assistant answer is committed once the stub stream completes.
    await expect(page.locator('[data-testid="assistant-answer"]')).toContainText(
      "Réponse de test."
    );
  });
});

test.describe("chat redesign — centered → bottom transition (#249)", () => {
  // AC: sending a first message anchors the input at the bottom with the thread above
  test("input moves from centered welcome to the bottom after the first message", async ({
    page,
  }) => {
    await stubChatStream(page);
    await page.goto("/");

    const input = page.getByRole("textbox", { name: /votre question/i });
    const welcomeCenterY = (await input.boundingBox())!.y;

    await input.fill("Bonjour");
    await page.keyboard.press("Enter");

    // After sending, the conversation state is active and the thread is shown above.
    await expect(page.getByTestId("conversation-state")).toBeVisible();
    await expect(page.getByText("Bonjour")).toBeVisible();

    // The input is now anchored lower than its centered welcome position.
    const conversationInputY = (await input.boundingBox())!.y;
    expect(conversationInputY).toBeGreaterThan(welcomeCenterY);
  });

  // AC: Shift+Enter inserts a newline instead of submitting
  test("Shift+Enter inserts a newline and does not submit", async ({ page }) => {
    await stubChatStream(page);
    await page.goto("/");

    const input = page.getByRole("textbox", { name: /votre question/i });
    await input.fill("Ligne un");
    await page.keyboard.press("Shift+Enter");
    await page.keyboard.type("Ligne deux");

    // Still on the welcome state (no submit) and the value contains a newline.
    await expect(page.getByTestId("welcome-state")).toBeVisible();
    await expect(input).toHaveValue("Ligne un\nLigne deux");
  });
});
