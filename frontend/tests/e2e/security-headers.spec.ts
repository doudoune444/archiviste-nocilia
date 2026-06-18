// AC: PLATFORM-004 AC-3 — nonce-based CSP + security headers parity with gateway.
// Asserts that:
//   1. Content-Security-Policy is present and at least as strict as the gateway
//      (no 'unsafe-inline', includes base-uri 'none', form-action 'self').
//   2. X-Content-Type-Options: nosniff is present.
//   3. Referrer-Policy: strict-origin-when-cross-origin is present.
//   4. The page hydrates without CSP violations (no console errors on navigation).
import { test, expect } from "@playwright/test";

test.describe("security headers (PLATFORM-004 AC-3)", () => {
  test("home page emits all three required security headers", async ({
    page,
  }) => {
    // Navigate and capture the response headers directly.
    const response = await page.goto("/");
    expect(response?.status()).toBe(200);

    const headers = response?.headers() ?? {};

    // AC-3 (1): CSP must be present.
    const csp = headers["content-security-policy"];
    expect(csp, "Content-Security-Policy header must be present").toBeTruthy();

    // Must include nonce directive (no static unsafe-inline).
    expect(csp).toMatch(/'nonce-[A-Za-z0-9+/=_-]+'/);
    // Must NOT include 'unsafe-inline'.
    expect(csp).not.toContain("'unsafe-inline'");

    // Gateway parity directives.
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("base-uri 'none'");
    expect(csp).toContain("form-action 'self'");
    expect(csp).toContain("img-src 'self' data:");

    // AC-3 (2): nosniff.
    expect(headers["x-content-type-options"]).toBe("nosniff");

    // AC-3 (3): Referrer-Policy.
    expect(headers["referrer-policy"]).toBe("strict-origin-when-cross-origin");
  });

  test("pages hydrate without CSP console violations", async ({ page }) => {
    // Collect any console errors that mention CSP violations.
    const cspViolations: string[] = [];
    page.on("console", (msg) => {
      if (
        msg.type() === "error" &&
        msg.text().toLowerCase().includes("content security policy")
      ) {
        cspViolations.push(msg.text());
      }
    });

    await page.goto("/");

    // Wait for full hydration — check heading is visible (RSC rendered).
    await expect(
      page.getByRole("heading", { name: /Bienvenue aux archives de Nocilia/i })
    ).toBeVisible();

    // No CSP violations during navigation and hydration.
    expect(
      cspViolations,
      `CSP violations: ${cspViolations.join("; ")}`
    ).toHaveLength(0);
  });
});
