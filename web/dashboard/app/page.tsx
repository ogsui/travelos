export default function Home() {
  return (
    <main>
      <p className="eyebrow">TravelOS</p>
      <h1>Dashboard scaffold is running.</h1>
      <p className="lede">
        Approvals, the unified inbox and CRM views arrive in tasks 017 and later. This placeholder
        exists so the monorepo, container stack and deployment path are testable from day one.
      </p>
      <dl>
        <div>
          <dt>Mode</dt>
          <dd>test (synthetic data only)</dd>
        </div>
        <div>
          <dt>API</dt>
          <dd>
            <code>GET /health</code> and <code>GET /ready</code>
          </dd>
        </div>
        <div>
          <dt>Mobile-first</dt>
          <dd>Built for approving quotes from a phone.</dd>
        </div>
      </dl>
    </main>
  );
}
