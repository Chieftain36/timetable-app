---
name: safe-frontend-refactor
description: Apply small approved HTML/CSS/JavaScript improvements while preserving FastAPI integrations, authorization, timetable logic and regression safety; use for implementation and code review.
---

# Safe frontend refactor
1. Read `AGENTS.md`, user-approved scope and existing frontend handlers; identify affected selectors and API requests before editing.
2. Work on a dedicated branch, make a minimal change. Do not rewrite unrelated pages or replace the stack.
3. Preserve endpoints, payload keys, IDs, form names, roles, data operations and confirmation/error behavior.
4. Never alter or exercise destructive generation/database operations without approval and disposable test data.
5. Inspect real tests before running. Current `package.json` lists scripts not in the published repo: do not invent successes.
6. Validate at 1440, 768 and 390 px where a browser is available, including dark/light modes if supported; test keyboard and focus states.
7. Regression-check login/navigation, CRUD, manual and weekly scheduling, conflict checking, generator form as relevant.
8. Report exact commands/results, screenshots only from the real app, missing checks and rollback steps; provide a reviewable PR and do not merge or deploy without explicit approval.
