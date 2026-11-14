import { expect, test } from "@playwright/test";

const SESSION_COOKIE = "travelos_dashboard_session";

test.describe("dashboard sign in", () => {
  test("offers a staff sign-in form without a code field", async ({ page }) => {
    await page.goto("/login");

    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await expect(page.getByLabel("Email")).toBeVisible();
    await expect(page.getByLabel("Password")).toBeVisible();
    await expect(page.getByLabel("Authentication code")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  });

  test("sends a visitor without a session to sign in", async ({ page }) => {
    await page.goto("/");

    await expect(page).toHaveURL(/\/login$/);
  });

  test("fails closed when the API cannot be reached", async ({ page, context }) => {
    // A session cookie exists, but the API is down: the dashboard must show no
    // data at all rather than an empty or stale shell.
    await context.addCookies([
      {
        name: SESSION_COOKIE,
        value: "synthetic-token-for-e2e",
        url: "http://127.0.0.1:3000",
      },
    ]);

    await page.goto("/");

    await expect(page.getByRole("heading", { name: "API unavailable" })).toBeVisible();
    await expect(page.getByText("No data is shown while the connection is down.")).toBeVisible();
  });
});
