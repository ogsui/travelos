import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.E2E_PORT ?? 3100);
const BASE_URL = `http://127.0.0.1:${PORT}`;

/**
 * The dashboard needs no database for this slice's smoke test: the login page
 * is public and an unauthenticated request fails closed. Full approval flows
 * get their own specs as later slices land.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: "list",
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
  },
  projects: [
    { name: "mobile", use: { ...devices["Pixel 7"] } },
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: {
    // Use a dedicated port so a local dev server on 3000 is never mistaken for
    // the app under test.
    command: `npx next dev --port ${PORT}`,
    url: `${BASE_URL}/login`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    // No API is running in this slice's smoke test; the app must fail closed.
    env: { API_BASE_URL: "http://127.0.0.1:9" },
  },
});
