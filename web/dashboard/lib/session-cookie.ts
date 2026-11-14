/** Pure session-cookie helpers, kept free of Next.js request context so they
 * can be unit tested without a running server.
 */

export const SESSION_COOKIE = "travelos_dashboard_session";

const CLOCK_SKEW_SECONDS = 30;
const MAX_COOKIE_AGE_SECONDS = 60 * 60 * 24 * 30;

/** Remaining cookie lifetime, leaving room for clock skew and dropping past dates. */
export function sessionMaxAgeSeconds(expiresAt: string, now: Date = new Date()): number {
  const expires = Date.parse(expiresAt);
  if (Number.isNaN(expires)) {
    return 0;
  }
  const seconds = Math.floor((expires - now.getTime()) / 1000) - CLOCK_SKEW_SECONDS;
  return Math.max(0, Math.min(seconds, MAX_COOKIE_AGE_SECONDS));
}

/** Production deployments are HTTPS-only unless explicitly marked otherwise. */
export function secureCookies(): boolean {
  return process.env.NODE_ENV === "production" && process.env.SESSION_COOKIE_SECURE !== "false";
}

export function sessionCookieOptions(maxAge: number) {
  return {
    httpOnly: true,
    sameSite: "lax" as const,
    secure: secureCookies(),
    path: "/",
    maxAge,
  };
}
