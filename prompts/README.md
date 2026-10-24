# prompts/

One file per LLM prompt, versioned. Created by the tasks that need them (parser, conversation, support, content, analytics digest).

Format of each file:
```
---
name: inquiry_parser
version: 1
data_class: customer        # synthetic | customer | public
input_schema: schemas/inquiry_input.json
output_schema: schemas/requirement.json
eval_set: data/eval/inquiry_gold.jsonl
---
<system prompt text; untrusted content is always passed as delimited DATA, never concatenated into instructions>
```
Rules: bump `version` on any change; re-run the eval and store results in `data/eval/results/`; never put customer data in this folder.
