import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiRequest } from "@/lib/api";

type FetchMock = ReturnType<typeof vi.fn>;

function fakeResponse(body: string, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => body,
  } as unknown as Response;
}

function calls(fetchMock: FetchMock): [string, RequestInit][] {
  return fetchMock.mock.calls as unknown as [string, RequestInit][];
}

let fetchMock: FetchMock;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  vi.stubEnv("API_BASE_URL", "https://api.example.test/");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("apiRequest", () => {
  it("sends the bearer token, disables caching and parses JSON", async () => {
    fetchMock.mockResolvedValue(fakeResponse(JSON.stringify({ id: "user-1" })));

    const payload = await apiRequest<{ id: string }>("/auth/me", { token: "secret-token" });

    expect(payload).toEqual({ id: "user-1" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = calls(fetchMock)[0];
    expect(url).toBe("https://api.example.test/auth/me");
    expect(init.method).toBe("GET");
    expect(init.cache).toBe("no-store");
    expect(init.headers).toMatchObject({
      Accept: "application/json",
      Authorization: "Bearer secret-token",
    });
    expect(init.headers).not.toHaveProperty("Content-Type");
    expect(init.body).toBeUndefined();
  });

  it("serialises a JSON body and sets the content type", async () => {
    fetchMock.mockResolvedValue(fakeResponse(JSON.stringify({ ok: true }), 201));

    await apiRequest("/auth/login", {
      method: "POST",
      body: { email: "staff@example.test", password: "synthetic-pass" },
    });

    const [, init] = calls(fetchMock)[0];
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({ "Content-Type": "application/json" });
    expect(init.headers).not.toHaveProperty("Authorization");
    expect(init.body).toBe(JSON.stringify({ email: "staff@example.test", password: "synthetic-pass" }));
  });

  it("returns null for an empty successful response", async () => {
    fetchMock.mockResolvedValue(fakeResponse("", 204));

    await expect(apiRequest("/auth/logout", { method: "POST", token: "t" })).resolves.toBeNull();
  });

  it("maps an API error code onto ApiError", async () => {
    fetchMock.mockResolvedValue(fakeResponse(JSON.stringify({ error: "mfa_required" }), 401));

    await expect(apiRequest("/auth/login", { method: "POST" })).rejects.toMatchObject({
      name: "ApiError",
      status: 401,
      code: "mfa_required",
    });
  });

  it("falls back to request_failed when the error body is not JSON", async () => {
    fetchMock.mockResolvedValue(fakeResponse("<html>bad gateway</html>", 502));

    await expect(apiRequest("/approvals")).rejects.toMatchObject({
      status: 502,
      code: "request_failed",
    });
  });

  it("maps a transport failure onto api_unavailable", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));

    const error = await apiRequest("/auth/me").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 0, code: "api_unavailable" });
  });

  it("fails closed in production when API_BASE_URL is missing", async () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("API_BASE_URL", "");

    await expect(apiRequest("/auth/me")).rejects.toMatchObject({
      status: 0,
      code: "api_not_configured",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
