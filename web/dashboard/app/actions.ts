"use server";

import { redirect } from "next/navigation";

import { ApiError } from "@/lib/api";
import type { LoginState } from "@/lib/auth";
import { clearSession, getSessionToken, login, logout, storeSession } from "@/lib/session";

const GENERIC_ERROR = "Something went wrong. Try again.";
const UNAVAILABLE_ERROR = "The API is unavailable. Try again shortly.";

function field(formData: FormData, name: string, maxLength: number, trim = true): string {
  const value = formData.get(name);
  if (typeof value !== "string") {
    return "";
  }
  return (trim ? value.trim() : value).slice(0, maxLength);
}

function failure(error: unknown): LoginState {
  if (!(error instanceof ApiError)) {
    return { status: "error", message: GENERIC_ERROR };
  }
  switch (error.code) {
    case "mfa_required":
      return {
        status: "mfa_required",
        message: "Enter the 6-digit code from your authenticator app.",
      };
    case "invalid_mfa_code":
      return { status: "mfa_required", message: "That code is not valid. Try again." };
    case "invalid_credentials":
      return { status: "error", message: "Email or password is incorrect." };
    case "mfa_enrollment_required":
      return {
        status: "error",
        message: "This account must enrol MFA before signing in. Ask the owner to enrol it.",
      };
    case "api_unavailable":
    case "api_not_configured":
      return { status: "error", message: UNAVAILABLE_ERROR };
    default:
      return { status: "error", message: GENERIC_ERROR };
  }
}

export async function loginAction(_previous: LoginState, formData: FormData): Promise<LoginState> {
  const email = field(formData, "email", 320);
  const password = field(formData, "password", 1_024, false);
  const totpCode = field(formData, "totp_code", 10);

  if (!email || !password) {
    return { status: "error", message: "Enter your email and password." };
  }

  try {
    const response = await login({ email, password, totpCode: totpCode || undefined });
    await storeSession(response);
  } catch (error) {
    return failure(error);
  }

  redirect("/");
}

export async function logoutAction(): Promise<void> {
  const token = await getSessionToken();
  if (token) {
    try {
      await logout(token);
    } catch {
      // The local cookie is cleared even when the API cannot be reached; the
      // server-side session then expires on its own.
    }
  }
  await clearSession();
  redirect("/login");
}
