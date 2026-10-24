# prompts/

One Markdown file per prompt version. The gateway loads every `*.md` here through
`llm/prompts.py`; `README.md` is ignored. Prompts are data, not code: changing a
prompt does not require a Python deploy, but every change bumps `version`.

## File format

```
---
name: inquiry_parser
version: 1
data_class: customer              # synthetic | customer | public
input_schema: schemas/inquiry_input.json
output_schema: schemas/requirement.json
eval_set: data/eval/inquiry_gold.jsonl
temperature: 0.0
max_tokens: 1024
trusted: [agency_name]
sensitive: [customer_name]
allow: {"intent": ["book", "cancel"]}
---

## System
You extract a TravelOS requirement. Answer only with JSON.
The agency is {{agency_name}}.

## User
Customer: {{customer_name}}
Message: {{message}}
```

- `name` is a lowercase slug (`[a-z][a-z0-9_]*`), `version` is a positive
  integer. Two files with the same name and version are a startup error.
- The registry resolves the highest version when no version is requested;
  callers may pin a version explicitly.
- `data_class` is the prompt's declared data class. It decides which providers
  may run the prompt: `customer` and `public` need a provider with
  `no_training=true` and `synthetic_only=false`. Synthetic data may use any
  provider (but never a training-allowed provider with real data).
- `input_schema` and `output_schema` are paths relative to this directory, or
  inline JSON objects. They use the JSON Schema subset described in
  `src/travelos/llm/schema.py`; `output_schema` enables JSON validation and the
  one-repair retry.
- `eval_set` is a pointer, not a guarantee. Re-run the matching eval after a
  version bump and store results under `data/eval/results/`.
- `temperature` (0–2, default 0) and `max_tokens` (default 1024) are sent to the
  provider. Keep `max_tokens` inside the model's documented output limit.
- `trusted` lists variables allowed inside `## System`. Every system variable
  must be listed here and must come from code, never from a customer message.
- `sensitive` lists variables that are always placeholder-bound, even when no
  PII pattern matches (for example a customer's name).
- `allow` maps top-level output fields to permitted string values. It is checked
  in addition to the output schema and is useful for classification labels.

## Rendering rules

- A body without `## System` / `## User` headings is treated as the system
  prompt. Text before the first heading belongs to the system section.
- Variables use `{{name}}`. A missing variable raises `PromptRenderError`; it
  never renders a literal `{{name}}`.
- A variable in the user section is untrusted: it is sanitised (control tokens
  and instruction-like lines removed), truncated and wrapped in a delimited
  `<<<UNTRUSTED_DATA name="...">>>` block. Customer text never enters the system
  section.
- A variable in the system section must be in `trusted`. This is a code-owned
  value such as the agency name, not a customer message.
- Placeholders (`[[EMAIL_1]]`) replace PII before rendering. Do not put real
  customer data, keys or passport numbers in this directory.

## Versioning checklist

1. Copy the file to a new version (or bump `version` in place only if the old
   version is not in use anywhere).
2. Bump `version` for any change to the system text, user template, schema,
   temperature or allow-list.
3. Run `make eval` (or the matching test) and store the result.
4. Note the change in the task report; never edit a released prompt silently.
