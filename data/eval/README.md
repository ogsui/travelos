# Evaluation data

Gold sets, adversarial cases and recorded eval results (tasks 006/007/018). No
real customer data — every value here is obviously synthetic.

- `llm_injection.jsonl` — adversarial prompt-injection fixtures for task 006.
  One JSON object per line with `id`, `input` (the untrusted customer/supplier
  text) and `compromised_output` (what a fully hijacked model would return).
  `tests/eval/test_llm_injection.py` proves every compromised output fails the
  declared schema or allow-list and that delimiter/control-token escapes are
  neutralised. `tests/integration/test_llm_gateway.py` runs the same fixtures
  through the real gateway and asserts `schema_error` rows plus human tasks.
- `results/` — recorded eval output per prompt version once the parser/reply/
  content evals land (tasks 007/018). Do not treat a missing result as a pass.
