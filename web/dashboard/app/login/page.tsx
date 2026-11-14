import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { loginAction } from "@/app/actions";
import { LoginForm } from "@/components/login-form";
import { readCurrentUser, getSessionToken } from "@/lib/session";

export const metadata: Metadata = {
  title: "Sign in · TravelOS",
};

export default async function LoginPage() {
  const token = await getSessionToken();
  if (token) {
    const session = await readCurrentUser(token);
    if (session.kind === "ok") {
      redirect("/");
    }
  }

  return (
    <main className="auth-shell" id="main">
      <div className="auth-card">
        <p className="eyebrow">TravelOS</p>
        <h1>Sign in</h1>
        <p className="lede">Staff only. Use the account your owner created for you.</p>
        <LoginForm action={loginAction} />
      </div>
    </main>
  );
}
