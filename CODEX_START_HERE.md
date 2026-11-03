# Codex setup — Intelligent University Timetable Generator

The setup in this branch adds instructions and task prompts only. There are **no runtime code edits**. Review and merge this PR before expecting `AGENTS.md` and repo skills to appear in Codex tasks on `main`.

1. Open https://chatgpt.com/codex, sign in, connect GitHub and select `Chieftain36/timetable-app` on `main`.
2. Use the checked-in root `AGENTS.md` and three repo skills under `.agents/skills/` as standard project guidance.
3. Start with the text in `codex-prompts/01_INITIAL_AUDIT.md`. Ask Codex to report which instructions and skills it detected.
4. Review `docs/PROJECT_OVERVIEW.md`, `docs/CURRENT_FEATURES.md`, `docs/UI_UX_AUDIT.md` and `docs/DESIGN_ROADMAP.md` once Codex has created them in an audit PR.
5. Approve the design direction separately before implementing UI changes. Make one scoped change per branch and test browser behavior before merging.

Source of truth: `main` as inspected November 3, 2026. Important: generator writes records; with `replace_existing=true`, existing schedule rows are deleted and replaced. Do not use production DB to test. Do not commit .env/secrets/dumps. The current repository does not include several `tests/*.cjs` files referenced by `package.json`.

Reference docs: https://learn.chatgpt.com/docs/build-skills and https://learn.chatgpt.com/docs/agent-configuration/agents-md.
