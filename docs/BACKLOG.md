# Backlog

Unrelated findings discovered while working on a task go here (one line each: what, where, why it matters). Do not fix them inside an unrelated task.

- Container image digests have not been verified on this Windows host (no Docker installed); task 037 should pin digests, run a vulnerability scan and prove `docker compose up` on CI.
- MinIO's Docker Hub repository is archived; images are moving to `quay.io/minio/minio`. Re-check the pinned tag and healthcheck before production (task 037).
- Dashboard has no ESLint/component-test setup yet; add it with the first real UI in task 017.
