import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: "list",
  use: {
    baseURL: "http://localhost:3000",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    // WHY production build: security-header and CSP-nonce specs must run against
    // the standalone production server.  Dev mode tolerates inline/eval and will
    // not catch a broken nonce wiring (#3 in the adversarial review).
    // NOTE: npm run build currently fails on a pre-existing observability/page.tsx
    // export error being fixed in a separate slice.  The config is wired correctly;
    // run the e2e locally once that build fix lands.
    command: "npm run build && npm run start",
    url: "http://localhost:3000",
    reuseExistingServer: !process.env.CI,
    timeout: 300_000,
  },
});
