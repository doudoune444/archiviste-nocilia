// AC: AUTH-001 — login / logout smoke
//
// NOTE: These tests require a live gateway (GATEWAY_URL env set and responding).
// Without a live gateway or mock server the BFF API routes return 503 and the
// login form shows the upstream-unavailable French message rather than
// redirecting. In CI the tests will be skipped via the `skipIf` guard below.
//
// To run locally:
//   GATEWAY_URL=http://localhost:8080 npm run test:e2e -- tests/e2e/auth.spec.ts
//
// AC covered when live gateway is available:
//   valid login → logged-in header (email + "Se déconnecter")
//   logout → anonymous header ("Se connecter")
import { test, expect, Page } from "@playwright/test";

const hasLiveGateway = !!process.env["GATEWAY_URL"];

/**
 * Fill and submit the login form with the given credentials.
 * Assumes the page is already at /login.
 */
async function fillLoginForm(
  page: Page,
  email: string,
  password: string
): Promise<void> {
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.click('button[type="submit"]');
}

test.describe("auth flow (AUTH-001)", () => {
  // AC: /login renders a masked password field and a submit button
  test("login page renders the form with masked password field", async ({
    page,
  }) => {
    await page.goto("/login");

    await expect(page.locator('input[type="email"]')).toBeVisible();
    // AC: password field masked — type="password" so browser never echoes value
    await expect(page.locator('input[type="password"]')).toBeVisible();
    await expect(
      page.getByRole("button", { name: /Se connecter/i })
    ).toBeVisible();
  });

  // AC: sub-minimum password rejected client-side before submit
  test("rejects a password shorter than 12 characters before submitting", async ({
    page,
  }) => {
    await page.goto("/login");

    await page.fill('input[type="email"]', "user@example.com");
    await page.fill('input[type="password"]', "short");
    await page.click('button[type="submit"]');

    // Client-side validation fires before any network call. Scope to the
    // password field-error text (the page can carry a second role="alert" for
    // form-level errors, so a bare [role="alert"] selector is ambiguous).
    await expect(
      page.getByText(/au moins 12 caractères/i)
    ).toBeVisible();
  });

  // AC: valid login → logged-in header (email + "Se déconnecter")
  test("valid login redirects to / and shows authenticated header", async ({
    page,
  }) => {
    // Live-only: needs a gateway that authenticates. The offline tests above run
    // unconditionally; only this and the logout test below require a backend.
    test.skip(!hasLiveGateway, "GATEWAY_URL not set — requires live gateway");
    const testEmail = process.env["TEST_LOGIN_EMAIL"] ?? "member@example.com";
    const testPassword =
      process.env["TEST_LOGIN_PASSWORD"] ?? "correct-horse-battery";

    await page.goto("/login");
    await fillLoginForm(page, testEmail, testPassword);

    // On success: redirected to /
    await page.waitForURL("/");

    // AC: header shows email + "Se déconnecter"
    await expect(
      page.getByRole("link", { name: /Se déconnecter/i })
    ).toBeVisible();
    await expect(page.getByText(testEmail)).toBeVisible();
  });

  // AC: "Se déconnecter" ends session server-side; header returns to anonymous
  test("logout ends the session and header returns to anonymous state", async ({
    page,
  }) => {
    test.skip(!hasLiveGateway, "GATEWAY_URL not set — requires live gateway");
    // Navigate to /logout which calls the gateway logout endpoint server-side.
    await page.goto("/logout");

    // Redirected to /
    await page.waitForURL("/");

    // AC: header shows anonymous links
    await expect(
      page.getByRole("link", { name: /Se connecter/i })
    ).toBeVisible();
    // "Se déconnecter" must not appear
    await expect(
      page.getByRole("link", { name: /Se déconnecter/i })
    ).not.toBeVisible();
  });
});
