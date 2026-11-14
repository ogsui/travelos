/** Server-only HTTP client for the TravelOS API.
 *
 * Every dashboard request runs on the Next.js server: the browser only holds an
 * httpOnly session cookie, never an API token. This module must not be imported
 * from a client component.
 */

const DEFAULT_API_URL = "http://localhost:8000";
const TIMEOUT_MS = 10_000;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super(code);
    this.name = "ApiError";
  }
}

type RequestOptions = {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  token?: string;
  body?: unknown;
};

function apiBaseUrl(): string {
  const configured = process.env.API_BASE_URL?.trim();
  if (configured) {
    return configured.replace(/\/+$/, "");
  }
  if (process.env.NODE_ENV === "production") {
    // Fail closed: a deployed dashboard must name its API explicitly.
    throw new ApiError(0, "api_not_configured");
  }
  return DEFAULT_API_URL;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function errorCode(payload: unknown): string {
  return isRecord(payload) && typeof payload.error === "string" ? payload.error : "request_failed";
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.token) {
    headers.Authorization = `Bearer ${options.token}`;
  }
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}${path}`, {
      method: options.method ?? "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }
    throw new ApiError(0, "api_unavailable");
  }

  let text = "";
  try {
    text = await response.text();
  } catch {
    throw new ApiError(0, "api_unavailable");
  }

  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    throw new ApiError(response.status, errorCode(payload));
  }
  return payload as T;
}
