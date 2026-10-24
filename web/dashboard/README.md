# Dashboard

Next.js (App Router, TypeScript strict) staff dashboard. The scaffold is
intentional: the approval/inbox experience is built in task 017 and grows with
later tasks.

## Local commands

```sh
npm install
npm run dev       # http://localhost:3000
npm run typecheck
npm run build
```

The API base URL is provided as `NEXT_PUBLIC_API_BASE_URL` (see the root
`docker-compose.yml`). Only public configuration may use the `NEXT_PUBLIC_`
prefix; never put secrets in client bundles.

## Rules

- Mobile-first. Approvals are done from phones.
- Server-side authorization on every request, not just hidden buttons.
- No real customer data in development. Use `MODE=test` and synthetic data.
