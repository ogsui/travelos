import { currentSession } from "@/lib/session";
import { modeDescription, modeLabel } from "@/lib/runtime";

export default async function OverviewPage() {
  const session = await currentSession();
  if (session.kind !== "ok") {
    // The layout already renders the sign-in redirect or the connection screen.
    return null;
  }

  const user = session.user;
  const mode = modeLabel();

  return (
    <div className="stack stack-lg">
      <header className="page-header">
        <p className="eyebrow">Overview</p>
        <h1>Dashboard</h1>
        <p className="lede">Signed in as {user.email}.</p>
      </header>

      <section aria-label="Account and environment" className="grid">
        <article className="card">
          <h2>Account</h2>
          <dl className="meta">
            <div>
              <dt>Role</dt>
              <dd>
                <span className="role-badge">{user.role}</span>
              </dd>
            </div>
            <div>
              <dt>Email</dt>
              <dd>{user.email}</dd>
            </div>
            <div>
              <dt>MFA</dt>
              <dd>{user.mfa_enrolled ? "Enrolled" : "Not enrolled"}</dd>
            </div>
          </dl>
        </article>

        <article className="card">
          <h2>Session</h2>
          <p>
            Your API session is held in an httpOnly cookie on the dashboard server. Signing out
            revokes it with the API.
          </p>
        </article>

        <article className="card">
          <h2>Environment</h2>
          <p>
            <span className={`mode-badge mode-${mode}`}>{mode}</span> {modeDescription(mode)}
          </p>
        </article>
      </section>
    </div>
  );
}
