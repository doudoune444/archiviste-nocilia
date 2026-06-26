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
  // AC: / renders a text input and a send button
  test("chat page renders the question form", async ({ page }) => {
    await page.goto("/");

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

    await page.goto("/");

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
    await page.goto("/");

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

  // AC (#273): on an empty thread the composer is vertically centered in the
  // available space, not pinned to the top. Regression guard for the broken
  // page→shell→main height chain that collapsed `.page` to its content height.
  test("welcome composer is vertically centered, not pinned to the top", async ({
    page,
  }) => {
    await page.goto("/");

    const composer = page.getByRole("textbox", { name: /question/i });
    await expect(composer).toBeVisible();

    const box = await composer.boundingBox();
    const viewport = page.viewportSize();
    if (box === null || viewport === null) {
      throw new Error("composer box / viewport unavailable");
    }

    // A centered composer sits well below the top third; a collapsed-to-content
    // welcome state would leave it near the page top (< 30% of viewport height).
    const composerCenterY = box.y + box.height / 2;
    expect(composerCenterY).toBeGreaterThan(viewport.height * 0.3);
  });

  // AC: clicking a chip sends that exact question and switches to conversation
  // state with the input anchored to the bottom and the thread above.
  test("clicking a chip sends the question and transitions centered → bottom", async ({
    page,
  }) => {
    test.skip(!hasLiveGateway, "GATEWAY_URL not set — requires live gateway");

    await page.goto("/");

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

test.describe("chat follow-up pills (#355)", () => {
  // AC (#355): done.followups render as clickable pills under the assistant
  // answer; the raw ---SUIVI--- sentinel block never appears; a click relaunches
  // a query through the same send path (the followup text is echoed as the user
  // message). Gateway-free: the stream is mocked deterministically.
  const followupA = "Comment l'Archiviste a-t-il été désigné ?";
  const followupB = "Quels documents sont conservés dans les archives ?";

  async function mockFollowupStream(page: import("@playwright/test").Page) {
    await page.route("**/api/v1/chat/stream", async (route) => {
      const answer = `Voici la réponse des archives.\n---SUIVI---\n- ${followupA}\n- ${followupB}`;
      const followups = JSON.stringify([followupA, followupB]);
      const body =
        'event: meta\ndata: {"mode":"canon","conversation_id":"f1","request_id":"r"}\n\n' +
        `event: token\ndata: {"text":${JSON.stringify(answer)}}\n\n` +
        `event: done\ndata: {"citations":[],"usage":{},"retrieve_ms":0,"llm_ms":0,"followups":${followups}}\n\n`;
      await route.fulfill({
        status: 200,
        headers: { "content-type": "text/event-stream" },
        body,
      });
    });
  }

  test("renders followups as pills, hides the sentinel, and a click relaunches the query", async ({
    page,
  }) => {
    await mockFollowupStream(page);

    await page.goto("/");
    await page.fill('textarea[name="question"]', "Qui est l'Archiviste ?");
    await page.click('button[type="submit"]');

    // AC: the answer body is visible and the raw sentinel block is not.
    const answer = page.locator('[data-testid="assistant-answer"]');
    await expect(answer).toContainText("Voici la réponse des archives.");
    await expect(answer).not.toContainText("---SUIVI---");

    // AC: both follow-ups render as buttons.
    const pillA = page.getByRole("button", { name: followupA });
    const pillB = page.getByRole("button", { name: followupB });
    await expect(pillA).toBeVisible();
    await expect(pillB).toBeVisible();

    // AC: clicking a pill relaunches the query through the same send path — a
    // second assistant turn is streamed in response.
    await pillA.click();
    await expect(
      page.locator('[data-testid="assistant-answer"]')
    ).toHaveCount(2);
  });
});

test.describe("chat composer persistence (#273)", () => {
  // AC (#273): in a conversation the composer must ALWAYS stay visible — the
  // thread scrolls internally, the page (window) must never scroll the composer
  // out of the viewport. Uses a mocked long answer so the run is deterministic
  // and gateway-free; the live-gateway transition test stays gated above.
  test("composer stays in the viewport when the thread overflows", async ({
    page,
  }) => {
    const longAnswer = Array.from(
      { length: 200 },
      (_, i) => `Paragraphe ${i + 1} d'une très longue réponse des archives.`
    ).join("\n\n");
    await page.route("**/api/v1/chat/stream", async (route) => {
      const body =
        'event: meta\ndata: {"mode":"rag","conversation_id":"long","request_id":"r"}\n\n' +
        `event: token\ndata: {"text":${JSON.stringify(longAnswer)}}\n\n` +
        'event: done\ndata: {"citations":[]}\n\n';
      await route.fulfill({
        status: 200,
        headers: { "content-type": "text/event-stream" },
        body,
      });
    });

    await page.goto("/");
    await page.fill('textarea[name="question"]', "Raconte une longue histoire.");
    await page.click('button[type="submit"]');

    await expect(page.locator('[data-state="conversation"]')).toBeVisible();
    await expect(page.locator('[data-testid="assistant-answer"]')).not.toBeEmpty();

    // Precondition: the answer itself is taller than the viewport (intrinsic to
    // the content, true in both broken and fixed layouts), so the scroll-away
    // regression can actually manifest if the height chain is unbounded.
    const answerBox = await page
      .locator('[data-testid="assistant-answer"]')
      .boundingBox();
    const vp = page.viewportSize();
    if (answerBox === null || vp === null) {
      throw new Error("answer box / viewport unavailable");
    }
    expect(answerBox.height).toBeGreaterThan(vp.height);

    // Reading back through history must not scroll the composer away: scrolling
    // the window to the top is a no-op for a viewport-bounded chat surface.
    await page.evaluate(() => window.scrollTo(0, 0));

    const box = await page
      .getByRole("textbox", { name: /question/i })
      .boundingBox();
    const viewport = page.viewportSize();
    if (box === null || viewport === null) {
      throw new Error("composer box / viewport unavailable");
    }

    expect(box.y).toBeGreaterThanOrEqual(0);
    expect(box.y + box.height).toBeLessThanOrEqual(viewport.height + 1);
  });
});
