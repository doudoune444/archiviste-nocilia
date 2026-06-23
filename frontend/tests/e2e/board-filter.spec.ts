// AC BOARD-003: filter + sort controls are reflected in URL search params.
//
// These tests exercise only the controls and the URL — not ticket data — so they
// run without a gateway: the board page renders BoardControls even in its empty /
// error state (the controls are a primary affordance that drives a URL re-fetch).
// The playwright.config.ts webServer builds + starts the production server.
import { test, expect } from "@playwright/test";

test.describe("board category filter (BOARD-003)", () => {
  test("board page loads with filter controls visible", async ({ page }) => {
    await page.goto("/lacunes");

    // Filter controls must be present on the board page.
    const categorySelect = page.getByTestId("category-select");
    await expect(categorySelect).toBeVisible();

    const sortSelect = page.getByTestId("sort-select");
    await expect(sortSelect).toBeVisible();
  });

  test("selecting a sort option reflects in the URL", async ({ page }) => {
    await page.goto("/lacunes");

    const sortSelect = page.getByTestId("sort-select");
    await sortSelect.selectOption("date");

    // URL must now contain sort=date (shareable/bookmarkable).
    await expect(page).toHaveURL(/sort=date/);
  });

  test("sort=priority is the default and does not appear in the URL", async ({
    page,
  }) => {
    await page.goto("/lacunes");

    // Default state: sort=priority is omitted from the URL (clean URL).
    const url = new URL(page.url());
    expect(url.searchParams.has("sort")).toBe(false);
  });

  test("clear-category button removes the category param from the URL", async ({
    page,
  }) => {
    // Navigate with a pre-set category param.
    await page.goto("/lacunes?category=lore");

    // The clear button should be visible because category is active.
    const clearBtn = page.getByTestId("clear-category");
    await expect(clearBtn).toBeVisible();

    await clearBtn.click();

    // After clearing, the category param must be gone from the URL.
    await expect(page).not.toHaveURL(/category=/);
  });
});
