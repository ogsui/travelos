# Backlog

Unrelated findings discovered while working on a task go here (one line each: what, where, why it matters). Do not fix them inside an unrelated task.

- Container image digests have not been verified on this Windows host (no Docker installed); task 037 should pin digests, run a vulnerability scan and prove `docker compose up` on CI.
- MinIO's Docker Hub repository is archived; images are moving to `quay.io/minio/minio`. Re-check the pinned tag and healthcheck before production (task 037).
- Dashboard has no ESLint/component-test setup yet; add it with the first real UI in task 017.
- Encryption key rotation/re-encryption tooling does not exist yet; the `enc:v1:` prefix reserves the format, but before the first key rotation we need an audited re-encrypt command and a key-id convention (task 002 deliberately shipped a single active key).
- Login has no rate limiting or lockout yet (task 003); add per-IP and per-account throttling with audit + alerting before shadow/live, otherwise password guessing is unbounded.
- MFA has no recovery path. An owner/admin who loses their authenticator cannot log in. Decide an owner-approved recovery flow (one-time recovery codes or a second owner reset) before pilot; do not add bypass in a hurry.
- First-time MFA enrollment is gated only by the password. A stolen password could enroll an attacker's authenticator before the real owner does. Add an out-of-band check (email/SMS to the recorded owner number, or an existing owner confirming) before the first production owner enrollment.
- `users.email` uniqueness is case-sensitive while login matches `lower(email)`; normalise on write or use a `lower(email)` unique index before user management ships (task 003 keeps emails lower-case but does not enforce it at the DB).
- `MIN_PASSWORD_LENGTH` is defined but no user-management flow enforces it yet; enforce a length/complexity policy and breached-password check when staff accounts are created.
- Outbox dead letters create human tasks but nothing alerts on them yet; add a dashboard count + alert on `task_type=outbox.dead_letter` and `status=open` (task 017 / task 037).
- Timer defaults in `workflow/timers.py` are placeholders (15 min SLA, 7-day quote expiry, etc.); owner-approved values from `config/business_rules.yaml` must override them before shadow/live.
