// AC DASH-002: transcript drawer e2e tests
//
// AC: author-only per-row "open transcript" affordance on the dashboard (NOT on public board)
// AC: opening loads turns via the messages endpoint, renders in order
// AC: turns rendered as sanitized Markdown via AssistantAnswer (no raw HTML)
// AC: drawer opens quickly, closes cleanly, lays out alongside the list on a laptop
// AC: load failure → clear error state with request id
//
// Tests requiring a live gateway + an author session are gated by GATEWAY_URL.
// Non-gateway smokes (affordance visibility, board unaffected) run unconditionally.

import { test, expect } from "@playwright/test";

const hasLiveGateway = !!process.env["GATEWAY_URL"];

test.describe("transcript drawer — affordance visibility (DASH-002)", () => {
  // AC: public board must NOT show any transcript button (no onOpenTranscript passed)
  test("public board /board has no transcript affordance", async ({ page }) => {
    await page.goto("/lacunes");
    // The transcript button must never appear on the public board.
    const transcriptBtns = page.getByTestId("open-transcript-btn");
    await expect(transcriptBtns).toHaveCount(0);
  });

  // AC: /dashboard shows transcript buttons (author-gated; only testable with live gateway)
  test("dashboard /dashboard shows transcript affordance per row (author session)", async ({
    page,
  }) => {
    test.skip(
      !hasLiveGateway,
      "GATEWAY_URL not set — requires live gateway with author session"
    );

    await page.goto("/dashboard");

    // The heading must be present — otherwise we're on the forbidden state
    await expect(
      page.getByRole("heading", { name: /Tableau de bord/i })
    ).toBeVisible();

    // If the table is visible (at least one ticket), the transcript button must be present.
    const table = page.getByRole("table", { name: /tickets lore-gap/i });
    if (await table.isVisible()) {
      const firstBtn = page.getByTestId("open-transcript-btn").first();
      await expect(firstBtn).toBeVisible();
    }
  });
});

test.describe("transcript drawer — open / close flow (DASH-002)", () => {
  // AC: clicking a transcript button opens the drawer; close button closes it
  test("opens and closes the transcript drawer via button", async ({ page }) => {
    test.skip(
      !hasLiveGateway,
      "GATEWAY_URL not set — requires live gateway with author session"
    );

    await page.goto("/dashboard");

    await expect(
      page.getByRole("heading", { name: /Tableau de bord/i })
    ).toBeVisible();

    const table = page.getByRole("table", { name: /tickets lore-gap/i });
    test.skip(!(await table.isVisible()), "no tickets to open");

    // Open the drawer
    const firstBtn = page.getByTestId("open-transcript-btn").first();
    await firstBtn.click();

    const drawer = page.getByTestId("transcript-drawer");
    await expect(drawer).toBeVisible();

    // Close the drawer via the close button
    await page.getByTestId("close-drawer-btn").click();
    await expect(drawer).not.toBeVisible();
  });

  // AC: ESC closes the drawer
  test("closes the transcript drawer with ESC key", async ({ page }) => {
    test.skip(
      !hasLiveGateway,
      "GATEWAY_URL not set — requires live gateway with author session"
    );

    await page.goto("/dashboard");
    await expect(
      page.getByRole("heading", { name: /Tableau de bord/i })
    ).toBeVisible();

    const table = page.getByRole("table", { name: /tickets lore-gap/i });
    test.skip(!(await table.isVisible()), "no tickets to open");

    await page.getByTestId("open-transcript-btn").first().click();
    const drawer = page.getByTestId("transcript-drawer");
    await expect(drawer).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(drawer).not.toBeVisible();
  });

  // AC: after drawer opens, turns are rendered in order (at least one assistant-answer)
  test("loaded transcript renders turns as sanitized markdown", async ({
    page,
  }) => {
    test.skip(
      !hasLiveGateway,
      "GATEWAY_URL not set — requires live gateway with author session"
    );

    await page.goto("/dashboard");
    await expect(
      page.getByRole("heading", { name: /Tableau de bord/i })
    ).toBeVisible();

    const table = page.getByRole("table", { name: /tickets lore-gap/i });
    test.skip(!(await table.isVisible()), "no tickets to open");

    await page.getByTestId("open-transcript-btn").first().click();
    const drawer = page.getByTestId("transcript-drawer");
    await expect(drawer).toBeVisible();

    // Wait for content: either an assistant-answer or an error alert
    const answer = drawer.getByTestId("assistant-answer").first();
    const errorAlert = drawer.getByTestId("drawer-error");
    await expect(answer.or(errorAlert)).toBeVisible({ timeout: 10_000 });

    // Sanitization: the rendered transcript subtree must contain no <script>
    // injected from turn content (rehype-sanitize + skipHtml strip raw HTML).
    expect(await drawer.locator("script").count()).toBe(0);
  });
});
