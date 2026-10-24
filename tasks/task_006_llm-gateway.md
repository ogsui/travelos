# Task 006 — LLM gateway, redaction, prompt registry

Phase 1 · Depends on: 001, 003 · Requirements: CORE-3

## Read first
- AGENTS.md
- docs/02_ARCHITECTURE.md
- docs/07_SECURITY_COMPLIANCE.md
- docs/09_CODING_STANDARDS.md

## Needs from owner
- Choice of runtime LLM providers and keys (synthetic-only providers allowed for now).

## Scope
- `LLMProvider` interface + Fake (deterministic), OpenAI-compatible (covers OpenCode Zen/OpenRouter/local) and Anthropic providers.
- Policy flags per provider (`no_training`, `synthetic_only`) enforced against data class (`synthetic|customer|public`).
- PII redaction/placeholder service with server-side reversible mapping.
- Prompt registry loading `prompts/*.md` with version, schemas and eval-set pointers.
- Structured output validation, one repair retry, fallback chain, human-task escalation.
- Cost/latency/metadata logging to `llm_calls` (redacted), budgets, dev response cache.
- Injection-defence helpers: delimiting untrusted text, instruction stripping, output allow-lists.

## Acceptance criteria
- Sending `customer` data to a `synthetic_only` provider raises (test).
- Schema failure -> retry -> human task (test).
- Cost recorded per call.
- Injection fixture set cannot cause out-of-schema outputs or tool calls.

## Human gates / approvals
- None (no tool execution in this layer by design).

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-006: ...`, write the report from AGENTS.md, then STOP.
