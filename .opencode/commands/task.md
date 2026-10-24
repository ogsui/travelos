---
description: Implement one task file end to end (usage: /task 006)
---
Implement the task whose number is: $ARGUMENTS. Its file is in `tasks/` and is named like `task_006_<name>.md` (list the folder to find it).

1. Read `AGENTS.md`, every doc listed under "Read first" in the task, and the existing code you will touch.
2. If the task lists "Needs from owner" items that are not present in the repo, stop and ask for them (or build against mocks as the task allows).
3. Write failing tests first (for LLM components, add cases to the eval set), then implement.
4. Touch only what the task needs. Log unrelated findings in `docs/BACKLOG.md`.
5. Run `make check` (and `make eval` if the task has evals). Fix until green.
6. Update stale docs. Fill/refresh the "Stack & commands" section of `AGENTS.md` if commands changed.
7. Commit as `task-$ARGUMENTS: <summary>`.
8. Report: what changed, decisions and why, uncertainties, how to run, what the human should review. STOP. Do not start another task.
