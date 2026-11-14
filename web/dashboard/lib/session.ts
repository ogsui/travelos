/** Server-side session handling.
 *
 * The API token from `POST /auth/login` is stored in an httpOnly cookie on the
 * dashboard origin and sent as a bearer token from the Next.js server. The
 * browser never sees it, so there is no token in client bundles or JavaScript.
 */

import { cookies } from "next/headers";
import { cache } from "react";

import { apiRequest, ApiError } from "@/lib/api";
import type { StaffUser } from "@/lib/auth";
import { SESSION_COOKIE, sessionCookieOptions, sessionMaxAgeSeconds } from "@/lib/session-cookie";

export { SESSION_COOKIE, sessionCookieOptions, sessionMaxAgeSeconds };

export type LoginResponse = {
  user: StaffUser;
  access_token: string;
  expires_at: string;
  csrf_token: string;
};

export type Credentials = {
  email: string;
  password: string;
  totpCode?: string;
};

export type SessionState =
  | { kind: "ok"; user: StaffUser }
  | { kind: "unauthenticated" }
  | { kind: "unavailable" };

export async function getSessionToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(SESSION_COOKIE)?.value ?? null;
}

export async function login(credentials: Credentials): Promise<LoginResponse> {
  const body: Record<string, string> = {
    email: credentials.email,
    password: credentials.password,
  };
  if (credentials.totpCode) {
    body.totp_code = credentials.totpCode;
  }
  return apiRequest<LoginResponse>("/auth/login", { method: "POST", body });
}

export async function storeSession(response: LoginResponse): Promise<void> {
  const maxAge = sessionMaxAgeSeconds(response.expires_at);
  if (maxAge <= 0) {
    // An already-expired token must not become a cookie that silently never works.
    throw new ApiError(0, "session_invalid");
  }
  const store = await cookies();
  store.set(SESSION_COOKIE, response.access_token, sessionCookieOptions(maxAge));
}

export async function clearSession(): Promise<void> {
  const store = await cookies();
  store.delete({ name: SESSION_COOKIE, path: "/" });
}

export async function logout(token: string): Promise<void> {
  await apiRequest<null>("/auth/logout", { method: "POST", token });
}

export async function readCurrentUser(token: string): Promise<SessionState> {
  try {
    const user = await apiRequest<StaffUser>("/auth/me", { token });
    return { kind: "ok", user };
  } catch (error) {
    if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
      return { kind: "unauthenticated" };
    }
    return { kind: "unavailable" };
  }
}

/** One read per request, shared by the layout and the page. */
export const currentSession = cache(async (): Promise<SessionState> => {
  const token = await getSessionToken();
  if (!token) {
    return { kind: "unauthenticated" };
  }
  return readCurrentUser(token);
});
