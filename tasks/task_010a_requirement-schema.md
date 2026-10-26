# Task 010a — Requirement schema and required-field rules

Phase 2 · Part of Task 010 · Depends on: 002, 007

## Read first
- AGENTS.md
- docs/01_REQUIREMENTS.md (INQ-1, INQ-2)
- docs/02_ARCHITECTURE.md
- docs/03_DATABASE.md
- docs/09_CODING_STANDARDS.md
- tasks/task_010_inquiry-parser.md
- data/schemas/inquiry.schema.json (`$defs.requirement` is the contract)

## Scope
- `inquiry/requirement.py`: the immutable Pydantic `Requirement` model whose field
  names, ranges and enums match `$defs.requirement`, plus `SourceSpan`,
  `ParsedRequirement` (confidence per field, source spans, ambiguous fields) and
  neutral-default/asserted-field accounting.
- `inquiry/rules.py`: deterministic required and recommended fields per trip
  type, conditional questions (infant age, confirmed dates, traveller documents)
  and missing/ambiguous field computation. No LLM calls in this slice.
- `inquiry/errors.py`: typed domain errors.
- Unit and property tests; docs and backlog updates.

## Acceptance criteria
- A serialized `Requirement` validates against the committed JSON schema, and
  every schema field is represented once (a drift test fails if either changes).
- Required fields are defined per trip type, including `unknown` (ask the trip
  type first) and `visa` (nationality required).
- Budget stays requested-but-optional: it is recommended, never blocking.
- An asserted (non-default) field with no source span is reported as a silent
  assumption; neutral defaults (`infants=0`, `urgency=normal`, `INR`) are not
  treated as customer assertions.
- Ambiguous fields are reported as missing with `reason="ambiguous"`, even when
  a value is present, so the parser can never quietly pick one reading.
- No LLM, network or database dependency in the module.

## Finish
Tests first, run `make check` (or direct equivalents on Windows), update docs,
commit `task-010a: ...`, write the report from AGENTS.md, then STOP.
