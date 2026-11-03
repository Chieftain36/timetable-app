# Codex guidance — Intelligent University Timetable Generator

## Mission
Improve the existing university scheduling SaaS without breaking working functionality, data, authorization, APIs or responsive UI. Repository code and verified tests are the source of truth; distinguish implemented, partial, planned and tested features.

## Current architecture
- Frontend: vanilla HTML, CSS and JavaScript in `index.html`; embedded compiled Tailwind CSS.
- Backend: FastAPI in `main.py`, Pydantic models in `models.py`, JWT/roles in `auth.py`.
- Scheduling: deterministic constraint solver in `scheduler.py`, not a trained AI model.
- Database: PostgreSQL and `schema.sql`; Docker Compose for local runtime.
- Do not migrate the frontend to React/Next.js without approval.
- Verify current role-specific pages and sprint features in code. Do not assume publication, persistent drafts, scoring, preferences or personal timetables exist.

## Mandatory safeguards
1. Begin by inspecting requested files and describing the exact work scope.
2. **Audit task: no application code, schema, test, or deployment modifications.** Audit Markdown files may be proposed in a reviewable branch.
3. Use short-lived branches/PRs. Do not merge to main, deploy, change secrets, run migrations, or alter production data without explicit approval.
4. Never request or commit credentials, `.env`, database URLs, backups or university personal data.
5. Preserve API endpoints, payloads, authentication/role checks, scheduling rules, validation and keyboard navigation.
6. `POST /api/v1/schedules/generate` persists records; `replace_existing=true` deletes all existing schedule entries before replacements within a transaction. **Do not invoke on real data or imply preview safety.**
7. Use synthetic data, accurately label mocks and unverified features. No fabricated screenshots, pass results or metrics.
8. Before a substantial redesign, show two or three coherent visual directions for approval. Start with readable light SaaS UI, restrained purple accents and an optional supported dark mode, not a generic template.
9. For any approved UI change: plan -> minimal diff -> accessible interaction -> verify desktop/tablet/mobile -> test flows -> report evidence/limitations -> PR review.
10. Inspect the actual available test files. Published `package.json` references multiple missing `tests/*.cjs` scripts; do not claim `npm test` passed without seeing it run.

## Code review
Flag permission leaks, destructive schedule writes, room/instructor/time conflicts, broken forms, missing error/loading/empty states, accessibility regressions, broken responsive layouts and invented functionality claims.

## Skills
- `.agents/skills/university-saas-design/SKILL.md`
- `.agents/skills/timetable-ux-specialist/SKILL.md`
- `.agents/skills/safe-frontend-refactor/SKILL.md`
