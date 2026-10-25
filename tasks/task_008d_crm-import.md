# Task 008d — CRM import umbrella

Phase 2 · Part of Task 008 · Depends on: 008c

The import work is split to keep each implementation bounded:

- [Task 008d1 — parsing and dry-run report](task_008d1_crm-import-parse.md)
- [Task 008d2 — approval-gated import API](task_008d2_crm-import-approval.md)

Complete 008d1 before starting 008d2. The import write path requires the locked A1 `crm.bulk_import` approval. Follow each sub-task's read-first list, acceptance criteria, tests, and commit instructions.
