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
- `sales_replies.jsonl` — the task 011c sales-reply rubric fixtures: 23 synthetic
  candidate replies with the customer message, the sources offered, the expected
  verdict (`allowed`/`blocked`), the expected claim kinds and the expected
  handoff triggers. Every source ref is namespaced (`quote:`, `inventory:` or
  `kb:`), and blocked cases cover invented prices, availability, policies,
  discounts, promises, wrong-kind sources, Hinglish/lakh/45k wording and
  injection text that must stay data. `tests/eval/test_sales_reply_eval.py`
  scores them with the real claim guard and handoff triggers.
- `sales_injection.jsonl` — compromised or clean sales replies. A blocked case
  names the claim kinds the guard must report; a clean case (empty
  `expected_blocked`) proves injection text alone does not block a safe reply.
  Handoff expectations prove a refund or visa request hidden in an instruction
  still reaches a human.
- `package_gold.jsonl` — the task 013b1 hand-checked package-component set: 11
  requirements with the normalized offers a search returned, the expected
  components (offer id, quantity, unit basis), the expected problem codes and an
  exact total in paise. Timestamps are offsets from one fixed clock so the file
  stays readable and reproducible. `tests/unit/test_packages_eval.py` derives
  the expectations from the builder's documented rules and refuses a component,
  quantity or total drift. Do not copy gold from a builder run.
- `results/` — recorded eval output per prompt version. The committed
  `inquiry_parser_v1.json` is a **fake mode** run (`gate.status =
  "not_measured"`), because no owner-configured model is available; it proves the
  plumbing, not accuracy. Provider mode is the only mode that measures quality
  and a fake run never overwrites a provider result. A missing result is not a
  pass. Re-run with `python -m travelos.inquiry.eval --mode fake|provider`. The
  committed `sales_replies_v1.json` is a deterministic run over fixtures; it
  measures the guard and handoff routing, and marks `helpful`/`on_brand` as
  `not_measured`. Re-run with `python -m travelos.conversation.eval`. The
  committed `package_components_v1.json` is a deterministic component-match run
  over the hand-checked package set (mean 1.0, gate 0.85); re-run with
  `python -m travelos.packages.eval`.

Regenerate the adversarial file with `make seed`; do not edit it by hand. The
gold and injection files are hand-checked and must be edited deliberately, with
the traceability test kept green.
