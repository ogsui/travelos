# Task 010d — Hand-checked parser gold set and eval runner

Phase 2 · Part of Task 010 · Depends on: 010a, 010b, 010c

## Read first
- AGENTS.md
- docs/01_REQUIREMENTS.md (INQ-1..INQ-4)
- docs/08_TESTING_EVAL.md
- data/eval/README.md
- tasks/task_010_inquiry-parser.md

## Scope
- `data/eval/inquiry_gold.jsonl`: a hand-checked set (at least two messages per
  synthetic category) where every gold value is traceable to the message. Do not
  copy `data/synthetic/inquiries.jsonl` gold values: that generator draws gold
  independently of the message (see `docs/BACKLOG.md`).
- `tests/eval/test_inquiry_parser_eval.py` plus a small runner that reports
  per-field precision/recall for required fields, confidence calibration,
  silent-assumption count and injection containment.
- Store one result file per prompt version under `data/eval/results/`; a missing
  result is not a pass.
- Update `docs/08_TESTING_EVAL.md` with how to run the eval and read results.

## Acceptance criteria
- Required-field accuracy is >= 95% on the hand-checked gold, or the honest
  measured baseline is recorded with the gap and the failing fields.
- Silent-assumption count is 0 (asserted field without a source span).
- Injection cases never alter the schema or trigger an action.
- Results include prompt name/version, model, date and per-field breakdown.
- The eval has a deterministic fake-provider mode for CI plumbing; accuracy
  gating only runs when the owner-configured provider is available, and the
  report says which mode produced the number.

## Finish
Run `make check` and the eval, update docs, commit `task-010d: ...`, write the
report from AGENTS.md, then STOP.
