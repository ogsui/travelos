import Link from "next/link";
import { redirect } from "next/navigation";

import { logoutAction } from "@/app/actions";
import { currentSession } from "@/lib/session";
import { modeLabel } from "@/lib/runtime";

export default async function AppLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const session = await currentSession();
  if (session.kind === "unauthenticated") {
    redirect("/login");
  }

  if (session.kind === "unavailable") {
    return (
      <main className="app-main" id="main">
        <div className="card">
          <p className="eyebrow">Connection</p>
          <h1>API unavailable</h1>
          <p className="lede">
            The dashboard cannot reach the TravelOS API right now. No data is shown while the
            connection is down.
          </p>
          <Link className="button" href="/">
            Try again
          </Link>
        </div>
      </main>
    );
  }

  const user = session.user;
  const mode = modeLabel();

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand">
          <span aria-hidden="true" className="brand-mark">
            T
          </span>
          <span className="brand-name">TravelOS</span>
          <span className={`mode-badge mode-${mode}`}>{mode}</span>
        </div>

        <div className="account">
          <span className="account-email">{user.email}</span>
          <span className="role-badge">{user.role}</span>
          <form action={logoutAction}>
            <button className="button-link" type="submit">
              Sign out
            </button>
          </form>
        </div>
      </header>

      <nav aria-label="Main" className="app-nav">
        <ul>
          <li>
            <Link aria-current="page" href="/">
              Overview
            </Link>
          </li>
        </ul>
      </nav>

      <main className="app-main" id="main">
        {children}
      </main>
    </div>
  );
}
