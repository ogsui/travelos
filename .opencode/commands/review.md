---
description: Review the current diff against TravelOS rules (usage: /review)
---
Review the uncommitted changes and the last commit. Do not modify files. Check against `AGENTS.md` hard rules and the task's acceptance criteria. Report, in order of severity:
1. Rule violations (float money, LLM in pricing, missing consent check, real data, hard-coded auto-send, invented API details, missing audit record, secrets).
2. Missing or weak tests (especially edge cases for money, state transitions, idempotency, and prompt injection).
3. Security/privacy issues (PII in logs or prompts, missing RBAC, unvalidated webhooks).
4. Design drift from `docs/02_ARCHITECTURE.md` and `docs/03_DATABASE.md`.
5. Anything unclear the human should decide.
Be specific: file, line, why it matters, suggested fix.
