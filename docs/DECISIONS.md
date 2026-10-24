# Decisions log (ADR-lite)

Format: `YYYY-MM-DD · title · decision · why · alternatives · decided by`

- 2026-10-24 · Scaffold choices · FastAPI with a plain-ASGI request middleware, hand-written JSON logging (no structlog dependency), Next.js 16 for the dashboard, all in one repo · fewer moving parts in the security-critical path; JSON output is small and fully under our redaction control; Next 16 removes the known postcss advisory in Next 15 · structlog/python-json-logger, separate frontend repo · engineering
- 2026-10-24 · Live gate in settings · `MODE=live` fails at settings construction unless `LIVE_CONFIRMED=yes` and database, Redis, secret and public URL are present; live also hides OpenAPI docs · fail closed before the process can serve traffic · validate lazily in a startup hook (rejected: traffic can arrive first) · engineering
- 2026-10-24 · Kill switches · capability switches are nested settings (`KILL_SWITCHES__<NAME>`), default OFF and reject unknown names · typos fail loudly instead of silently enabling nothing · free-form env keys · engineering
- 2026-10-24 · Field-level encryption · Fernet (authenticated symmetric encryption) over an `enc:v1:` prefix, key from `ENCRYPTION_KEY` behind a `KeyProvider` interface · authenticated ciphertext, a version prefix leaves room for rotation, and no passport/DOB/contact plaintext reaches the database or logs · pgcrypto (key material at the DB layer), application-level AES-GCM (hand-rolled nonce handling) · engineering
- 2026-10-24 · Append-only audit log · enforced by both ORM listeners and a PostgreSQL trigger that blocks UPDATE/DELETE/TRUNCATE · process-level guards alone are bypassed by raw SQL, so the database is the backstop; both layers give early errors in tests and real protection in production · revoking grants only (rejected: migrations and admin roles need DDL rights) · engineering
- 2026-10-24 · Real PostgreSQL for schema tests · `pgserver` (bundled PostgreSQL 16, Apache-2.0) as a dev-only dependency · CHECK constraints, partial unique indexes and triggers cannot be tested faithfully on SQLite, and this host has no Docker; CI still runs migrations against the compose database · SQLite, mocked engine, requiring a local PostgreSQL install · engineering
- (owner) Scraping per site: record here before enabling any `PublicPageScraper` site flag: site, robots.txt/ToS reviewed (yes/no), rationale, who accepted the risk.
- (owner) PortalSessionAdapter: record risk acceptance before enabling.
- (owner) Cold outreach lists: record legal basis per list before enabling.
- (owner/accountant) Tax tables and invoice formats: record sign-off date and version.
