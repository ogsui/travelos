# Data

Only obviously fake synthetic data belongs here. Real customer data, passports,
payment references and production exports must never be committed or copied
into this repository.

- `synthetic/` — generated datasets (task 007).
- `eval/` — gold sets and adversarial fixtures (tasks 007 and 018).

Generators are deterministic and seeded so tests and evals are reproducible.
