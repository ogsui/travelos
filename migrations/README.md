# Migrations

Alembic is installed and configured in task 002 (`database-core`). Until then
this directory exists so the repository layout matches
`docs/02_ARCHITECTURE.md`.

Rules for every migration:

- Backwards compatible: expand -> migrate -> contract.
- Never edit an applied migration; add a new one.
- Money columns are `*_paise BIGINT` plus a `currency CHAR(3)`.
- Append-only tables are protected at the database level, not only in code.
