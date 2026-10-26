# Evaluation data

Gold sets, adversarial cases and recorded eval results (tasks 006/007/010/018). No
real customer data — every value here is obviously synthetic.

- `llm_injection.jsonl` — adversarial prompt-injection fixtures for task 006.
  One JSON object per line with `id`, `input` (the untrusted customer/supplier
  text) and `compromised_output` (what a fully hijacked model would return).
  `tests/eval/test_llm_injection.py` proves every compromised output fails the
  declared schema or allow-list and that delimiter/control-token escapes are
  neutralised. `tests/integration/test_llm_gateway.py` runs the same fixtures
  through the real gateway and asserts `schema_error` rows plus human tasks.
- `adversarial_messages.jsonl` — the task 007 adversarial customer/supplier
  set, generated with the rest of the synthetic dataset. Categories are prompt
  injection, e-mail injection, scraped-page injection, abuse, PII fishing,
  refund pressure, fake urgency, wrong language and giant messages.
  `tests/eval/test_adversarial_dataset.py` checks coverage, obvious-fake
  patterns and a message over 4,000 characters. The parser/reply evals in
  task 018 consume the same file.
- `inquiry_gold.jsonl` — the task 010d hand-checked parser gold set: 32 synthetic
  messages, at least two per synthetic category, each with a full
  `gold_requirement`, the verbatim `evidence` quote(s) for every asserted field,
  `ambiguous_fields` and notes. Names and cities stay exactly as written
  (misspellings included), budgets are integer paise, and only values the message
  states are asserted. `tests/eval/test_inquiry_parser_eval.py` proves every gold
  quote appears in its message and that the categories match
  `data/synthetic/inquiries.jsonl`. Do not copy gold from that generator: it
  draws gold values independently of the message.
- `inquiry_injection.jsonl` — compromised inquiry-parser payloads that the
  parser must reject: an extra top-level action key, an invalid trip type, a
  quote the customer never wrote and a field filled without evidence. Each line
  names the expected error class; `run_injection_cases` scores containment.
- `results/` — recorded eval output per prompt version. The committed
  `inquiry_parser_v1.json` is a **fake mode** run (`gate.status =
  "not_measured"`), because no owner-configured model is available; it proves the
  plumbing, not accuracy. Provider mode is the only mode that measures quality
  and a fake run never overwrites a provider result. A missing result is not a
  pass. Re-run with `python -m travelos.inquiry.eval --mode fake|provider`.

Regenerate the adversarial file with `make seed`; do not edit it by hand. The
gold and injection files are hand-checked and must be edited deliberately, with
the traceability test kept green.
