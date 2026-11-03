# First Codex task — read-only technical and UX audit

Join the existing `Chieftain36/timetable-app` project as Senior Architect + UI/UX Designer + Frontend Engineer. Read `AGENTS.md` and all relevant repository files.

**DO NOT change application source, SQL, tests or production configuration.** No deployment, no production database connection.

Write on an audit branch:
1. `docs/PROJECT_OVERVIEW.md`: architecture, dependencies, routes, data flow, environment and test setup.
2. `docs/CURRENT_FEATURES.md`: role-specific feature matrix with `implemented / partial / planned / not verified` and file-level evidence.
3. `docs/UI_UX_AUDIT.md`: usability, visual hierarchy, forms, calendar, navigation, keyboard and responsive issues; separate browser-verified results from source-only analysis.
4. `docs/DESIGN_ROADMAP.md`: P0/P1/P2 improvements with scope, risk, acceptance criteria and dependencies.

Pay special attention to: README/package.json test references versus actual files, `replace_existing=true` destructive schedule replacement, deterministic algorithm rather than AI/ML, role access, and whether draft/publication/personal timetable/quality-score features are truly implemented. Never fabricate pass results or feature status.

Provide a ranked issue list and first UI proposal. Stop for approval before implementation.
