# Decisions log (ADR-lite)

Format: `YYYY-MM-DD · title · decision · why · alternatives · decided by`

- 2026-10-24 · Scaffold choices · FastAPI with a plain-ASGI request middleware, hand-written JSON logging (no structlog dependency), Next.js 16 for the dashboard, all in one repo · fewer moving parts in the security-critical path; JSON output is small and fully under our redaction control; Next 16 removes the known postcss advisory in Next 15 · structlog/python-json-logger, separate frontend repo · engineering
- 2026-10-24 · Live gate in settings · `MODE=live` fails at settings construction unless `LIVE_CONFIRMED=yes` and database, Redis, secret and public URL are present; live also hides OpenAPI docs · fail closed before the process can serve traffic · validate lazily in a startup hook (rejected: traffic can arrive first) · engineering
- 2026-10-24 · Kill switches · capability switches are nested settings (`KILL_SWITCHES__<NAME>`), default OFF and reject unknown names · typos fail loudly instead of silently enabling nothing · free-form env keys · engineering
- (owner) Scraping per site: record here before enabling any `PublicPageScraper` site flag: site, robots.txt/ToS reviewed (yes/no), rationale, who accepted the risk.
- (owner) PortalSessionAdapter: record risk acceptance before enabling.
- (owner) Cold outreach lists: record legal basis per list before enabling.
- (owner/accountant) Tax tables and invoice formats: record sign-off date and version.
