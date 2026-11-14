"use client";

import { useState } from "react";
import { useActionState } from "react";

import type { LoginAction, LoginState } from "@/lib/auth";

const INITIAL_STATE: LoginState = { status: "idle" };

export function LoginForm({ action }: { action: LoginAction }) {
  const [state, formAction, pending] = useActionState(action, INITIAL_STATE);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");

  const mfaRequired = state.status === "mfa_required";
  const error = state.status === "error" ? state.message : undefined;

  return (
    <form action={formAction} className="stack">
      {error ? (
        <p className="form-error" id="login-error" role="alert">
          {error}
        </p>
      ) : null}
      {mfaRequired && state.message ? (
        <p className="form-hint" role="status">
          {state.message}
        </p>
      ) : null}

      <div className="field">
        <label htmlFor="email">Email</label>
        <input
          autoComplete="username"
          id="email"
          inputMode="email"
          maxLength={320}
          name="email"
          onChange={(event) => setEmail(event.target.value)}
          required
          type="email"
          value={email}
          aria-describedby={error ? "login-error" : undefined}
          aria-invalid={error ? true : undefined}
        />
      </div>

      <div className="field">
        <label htmlFor="password">Password</label>
        <input
          autoComplete="current-password"
          id="password"
          maxLength={1024}
          name="password"
          onChange={(event) => setPassword(event.target.value)}
          required
          type="password"
          value={password}
          aria-describedby={error ? "login-error" : undefined}
          aria-invalid={error ? true : undefined}
        />
      </div>

      {mfaRequired ? (
        <div className="field">
          <label htmlFor="totp_code">Authentication code</label>
          <input
            autoComplete="one-time-code"
            autoFocus
            id="totp_code"
            inputMode="numeric"
            maxLength={6}
            name="totp_code"
            onChange={(event) => setCode(event.target.value)}
            pattern="[0-9]{6}"
            required
            type="text"
            value={code}
            aria-describedby="totp-help"
          />
          <p className="field-help" id="totp-help">
            6 digits from your authenticator app.
          </p>
        </div>
      ) : null}

      <button className="button" disabled={pending} type="submit">
        {pending ? "Signing in…" : mfaRequired ? "Verify code" : "Sign in"}
      </button>
    </form>
  );
}
