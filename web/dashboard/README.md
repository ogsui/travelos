# Dashboard

Next.js (App Router, TypeScript strict) staff dashboard. Task 017a provides the
shell, sign-in and test harness; the approval queue, review screen, inbox,
human tasks and kill-switch panel arrive in 017b-017e.

## Local commands

```sh
npm install
npm run dev        # http://localhost:3000
npm run lint
npm run test       # Vitest + Testing Library
npm run typecheck
npm run build
npx playwright install chromium   # once per machine
npm run e2e        # starts its own dev server on :3100
```

`make web-check` runs lint + unit tests + typecheck + build; `make web-e2e` runs
the browser suite. The Playwright config uses port 3100 so a dev server already
running on 3000 is never mistaken for the app under test.

## Configuration

| Variable | Purpose |
|---|---|
| `API_BASE_URL` | Server-side API origin (e.g. `http://api:8000` in compose). Required in production; development falls back to `http://localhost:8000`. |
| `MODE` | `test` / `shadow` / `live`; shown as the header badge. Defaults to `test`. |
| `SESSION_COOKIE_SECURE` | `false` only for a local plain-http stack. Cookies are `secure` in production otherwise. |

The browser never receives an API token or the API origin. `POST /auth/login`
runs in a server action, and the returned access token is stored in the httpOnly
`travelos_dashboard_session` cookie; every API call is made by the Next.js server
with `Authorization: Bearer`. There is no `NEXT_PUBLIC_` API setting.

Fail-closed behaviour: with no `API_BASE_URL` in production the dashboard calls
no API (`api_not_configured`), and if the API is unreachable it shows an
"API unavailable" screen rather than an empty shell. Sending nothing is always
better than showing stale approvals.

## Rules

- Mobile-first. Approvals are done from phones.
- Server-side authorization on every request, not just hidden buttons.
- No secrets in client bundles or client components.
- No real customer data in development. Use `MODE=test` and synthetic data.
