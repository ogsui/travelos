import { afterEach, describe, expect, it, vi } from "vitest";

import {
  SESSION_COOKIE,
  secureCookies,
  sessionCookieOptions,
  sessionMaxAgeSeconds,
} from "@/lib/session-cookie";

const NOW = new Date("2026-11-14T09:00:00.000Z");

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("sessionMaxAgeSeconds", () => {
  it("subtracts the clock-skew allowance from the token lifetime", () => {
    expect(sessionMaxAgeSeconds("2026-11-14T10:00:00.000Z", NOW)).toBe(3600 - 30);
  });

  it("never returns a negative lifetime", () => {
    expect(sessionMaxAgeSeconds("2026-11-14T08:00:00.000Z", NOW)).toBe(0);
  });

  it("drops unparseable expiry values", () => {
    expect(sessionMaxAgeSeconds("not-a-date", NOW)).toBe(0);
  });

  it("caps the cookie lifetime at 30 days", () => {
    expect(sessionMaxAgeSeconds("2027-11-14T09:00:00.000Z", NOW)).toBe(60 * 60 * 24 * 30);
  });
});

describe("secureCookies", () => {
  it("is off outside production", () => {
    vi.stubEnv("NODE_ENV", "test");
    expect(secureCookies()).toBe(false);
  });

  it("defaults on in production", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("SESSION_COOKIE_SECURE", "");
    expect(secureCookies()).toBe(true);
  });

  it("can be switched off for a local http deployment", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("SESSION_COOKIE_SECURE", "false");
    expect(secureCookies()).toBe(false);
  });
});

describe("sessionCookieOptions", () => {
  it("keeps the token out of JavaScript and scopes it to the dashboard", () => {
    vi.stubEnv("NODE_ENV", "test");

    expect(sessionCookieOptions(600)).toEqual({
      httpOnly: true,
      sameSite: "lax",
      secure: false,
      path: "/",
      maxAge: 600,
    });
    expect(SESSION_COOKIE).toBe("travelos_dashboard_session");
  });
});
