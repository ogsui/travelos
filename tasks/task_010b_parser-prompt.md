# Task 010b — Inquiry parser prompt v1 and gateway adapter

Phase 2 · Part of Task 010 · Depends on: 006, 010a

## Read first
- AGENTS.md
- docs/01_REQUIREMENTS.md (INQ-1, INQ-3)
- docs/07_SECURITY_COMPLIANCE.md
- docs/09_CODING_STANDARDS.md
- prompts/README.md
- src/travelos/llm/gateway.py, src/travelos/llm/prompts.py
- tasks/task_010_inquiry-parser.md
- tasks/task_010a_requirement-schema.md

## Scope
- `prompts/inquiry_parser.md` v1: English, Hindi and Hinglish; extract only what
  the message states; emit per-field confidence and source spans; return null for
  unknown values; no prices, taxes, visa rules or invented supplier facts.
- `prompts/schemas/inquiry_input.json` and `prompts/schemas/requirement.json`
  (the output schema includes the requirement plus evidence).
- `inquiry/parser.py`: call `LLMGateway.complete`, validate the structured result
  with `llm/schema.py`, build `ParsedRequirement`, one repair retry, then raise a
  typed error that becomes a human task. Untrusted message text never enters the
  system section.
- Tests for schema validation, confidence/span mapping, unsupported fields,
  conflicting values and prompt-injection containment.

## Acceptance criteria
- The gateway, not the adapter, performs provider selection, redaction,
  policy checks and cost/latency logging.
- Output that fails the declared schema cannot become a `Requirement`.
- A field with no supporting span is never populated; the parser reports it for
  clarification instead.
- Adversarial messages cannot change the schema, add actions/tools or set fields
  the customer did not state.
- The prompt is versioned and its evaluation set pointer is set (the gold set
  lands in 010d).

## Finish
Eval/test first, run `make check` (or direct equivalents on Windows), update docs,
commit `task-010b: ...`, write the report from AGENTS.md, then STOP.
