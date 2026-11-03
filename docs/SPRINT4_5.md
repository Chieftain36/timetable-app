# Sprint 4–5: generation and official personal timetables

Implementation branch: `feat/sprints-4-5`, based on `f4cdd31`. Source of acceptance scope: uploaded `1_6_Intelligent_University (2).xlsx`, US16–25. Existing vanilla frontend, FastAPI, current-user authorization and transaction/advisory-lock safeguards are retained. Selected prototype CSS/UI ideas from the uploaded `sprint45` directory were adapted; its automatic startup migration and scripts reading a Windows `.env` were not adopted.

## What is implemented

| Story | Implementation | Where to see it |
| --- | --- | --- |
| US16 Generate timetable | Saved teaching loads or named manual assignments; persistent draft, indeterminate running progress, completion log and measured heuristic quality score; explicit infeasibility errors | Admin → Generate timetable |
| US17 Instructor availability | Blocked slots excluded, preferred slots prioritized; hard rules always outrank soft preferences | Instructor → My availability; admin generation |
| US18 Room capacity/type | Active room capacity and lecture/practical type enforced; optional course override and required laboratory feature tags (`computer-lab`, `chemical-lab`) | Admin → Rooms (features); Generate timetable (course rules) |
| US19 Preferences | Avoid early classes, cutoff time, compact days, balanced weekdays, soft daily class limit and adjustable weights; persisted settings and Restore defaults | Generate timetable → Soft preferences |
| US20 Regenerate safely | Locked classes/IDs/placements retained; updated teaching loads applied; generation creates a draft version. Failed solver, SQL or validation rolls back the complete replacement; publication unchanged | Generate updated draft; calendar Lock/Unlock |
| US21 Weekly timetable | Day/week views and navigation, actual session details, empty states; optional teaching-period bounds mean out-of-period weeks are empty | Weekly timetable |
| US22 Student timetable | Admin links account to one active group. Student sees only that group's latest published snapshot, with clear unlinked/unpublished/empty states | Users → Connect students; Student → My timetable |
| US23 Instructor timetable | Active instructor profile derived from authenticated user; only own published sessions | Instructor → My timetable |
| US24 Search/filter | Course/group/instructor/room selectors plus text search, AND semantics, consistent grid/list, clear/no-matches states; equivalent server filters operate only after ownership scoping | Calendar filter bar |
| US25 Publish | Explicit confirmation, full hard-rule validation, immutable denormalized snapshot; later draft edits/regeneration do not affect the official copy | Admin → Weekly timetable → Publish draft / Published |

Lab categories are represented by practical rooms plus explicit feature tags; the existing valid campus room identifiers/capacity ranges remain in force. Set both the room's available tags and the course's required tags. A course override takes precedence over teaching-load delivery mode; without an override, lecture loads use lecture rooms and practice/lab loads use practical rooms. Manual placement/publication also checks explicit course rules and matching teaching-load modes.

## First installation

Use the **whole project**, not only `index.html`. The runtime needs `planning.py`, `database.py`, existing Python modules and the updated schema. With Docker Desktop running, execute `start.ps1` from the checkout. On a new PostgreSQL volume, Compose initializes all tables from the updated `schema.sql`. Bootstrap credentials remain private in the newly generated `.env`.

No dependencies were added. Existing Python and Node installation instructions still apply. No existing database is migrated at application startup.

## Upgrade an existing installation

**Do not delete the Docker volume or replace `.env`.** Back up the database using your normal private backup process before applying the additive migration. This branch does not rotate credentials or automatically touch an existing installation.

1. Stop the application container while retaining PostgreSQL/data:

   ```powershell
   docker compose stop app
   docker compose up -d db
   ```

2. After PostgreSQL is healthy, apply the reviewed migration from PowerShell in the project directory:

   ```powershell
   Get-Content -Raw .\migrations\004_planning.sql | docker compose exec -T db psql -U timetable -d timetable -v ON_ERROR_STOP=1
   if ($LASTEXITCODE -ne 0) { throw 'Migration failed. Keep the application stopped and inspect the error.' }
   ```

3. Rebuild and start the app:

   ```powershell
   docker compose up --build -d app
   docker compose logs --tail=50 app
   ```

`004_planning.sql` is transactional and repeatable. It adds planning settings, course room rules, student-group links, lock flags, immutable version records, optional teaching-period dates and room features. It does not delete schedules, choose student memberships, classify old rooms or publish anything. Legacy rooms without a type must be classified before the new generator can use them. Configure course overrides for courses whose mode differs from stored teaching-load mode.

For installations older than the previously audited schema, this migration assumes the existing core tables and Sprint 2/3 columns already exist. Review older upgrades separately rather than applying unrelated historic scripts blindly. The planning API reports 503 with the migration filename if its schema is absent. Existing-volume Docker init SQL does not rerun by itself.

## Demonstration flow

1. Sign in as admin. Create/verify users, instructor profiles, courses, groups, classified rooms and Mon–Sat time slots.
2. Set room feature tags for specialist laboratories. Add saved teaching loads with duration, count and delivery mode. The duration must match actual available slots.
3. In Users, assign student accounts to groups. Instructors can save their own availability.
4. In Generate timetable, set preferences and optional course room rules. Save a teaching period if only certain calendar weeks should contain sessions. Empty period dates explicitly mean an unbounded recurring template.
5. Generate updated draft; inspect the log/score and Review draft. Lock important sessions; edit teaching requirements and regenerate to verify the fixed placements stay.
6. Use all filters together and navigate dates. Dates outside the teaching period have an empty state.
7. Publish draft after review. Sign in separately as a student/instructor to verify the official personal schedule.
8. Change/regenerate the draft. Personal views keep showing the previous publication until the administrator publishes again.

The **Published** toggle is a read-only snapshot view. Lock/edit/delete actions are available only in the admin draft view. An invalid retained lock, insufficient room/slot/availability or a failed insert leaves the previous draft/version history unchanged. Locked classes must be unlocked before editing/deleting or removing their teaching requirement.

## API and semantics

New routes under `/api/v1/planning`:

- `GET/PUT /settings`: soft preferences, admin only.
- `GET/PUT /term`: optional inclusive teaching-period start/end, admin only.
- `GET /course-rules`, `PUT/DELETE /course-rules/{course_id}`: hard room type/features, admin only.
- `GET /student-links`, `PUT/DELETE /student-links/{user_id}`: group membership, admin only.
- `GET /status`: publication metadata; draft counts/version history only for admin; own group name only for student.
- `GET /timetable`: `source=draft|published`, optional `week_start`, group/room/instructor/course UUIDs and `q` (100 characters maximum).
- `PUT /locks/{schedule_id}`: lock/unlock a draft session, admin only.
- `POST /generate`: existing assignment/replace request shape, admin only. Saves versioned working draft; default UI requests replacement while preserving locks.
- `POST /publish` with `{"confirm":true}`: validates and snapshots the current draft, admin only.

Non-admin timetable requests always use the official snapshot, even if the caller requests `source=draft`. Supplied IDs/text only narrow authorized rows; they cannot widen ownership. Unlinked students get an empty list, not university-wide data. `/api/v1/schedules` is retained: admin reads current draft; non-admin reads official owned rows. A supplied different instructor ID on the legacy route is still rejected. Missing/deleted/stale-role accounts retain existing 401 protection.

Legacy `/schedules/generate` remains for contract compatibility and edits the working schedule, never a publication. Replacement through that route is blocked when locks exist; use `/planning/generate` for lock-preserving regeneration. No legacy endpoint can modify a publication record.

## Quality score and limits

The solver is deterministic greedy placement, **not AI/ML** and not a proof of global optimality or infeasibility. Candidate ranking uses instructor preferred slots and weighted soft cost. Hard-invalid placements are excluded regardless of preferences.

Reported score is `100 / (1 + weighted soft penalty / class count)`. Penalties include early hours, instructor/group gap hours, group daily classes beyond the preferred limit, and day-count variance. Details and formula are returned with the result; the UI explains that unmet soft preferences are not hard conflicts. A score below 100 may be unavoidable even in the best feasible arrangement. It is not a made-up percentage of completed work.

Progress is indeterminate while the basic solver runs in a worker thread; no fabricated percentage or solver optimality claim. The planning API bounds a run to 2000 requested classes and 2000 slots, with a 10-second placement budget. Budget exhaustion returns 503 and preserves the previous draft; it is not reported as proof of mathematical infeasibility. Large/complex workloads can still require a stronger solver or asynchronous job architecture. Version history here supports generation/publication evidence; full version restore, change requests and notifications from Sprint 6 are not claimed as implemented.

## Verification

Tests use isolated PostgreSQL schemas and synthetic accounts/data. Never set `SPRINT2_TEST_DSN` to production. Example commands refer to environment variables whose values you supply for a disposable test service:

```sh
python -m pytest -q
BROWSER_EXECUTABLE_PATH=/usr/bin/chromium npm test
RUN_BROWSER_E2E=1 BROWSER_EXECUTABLE_PATH=/usr/bin/chromium python -m pytest -q
```

With no test DSN, PostgreSQL tests skip explicitly. The optional real browser test additionally needs `RUN_BROWSER_E2E=1`; it launches temporary local Uvicorn against the test schema and closes it afterward. Browser mock tests are separate from real HTTP/database integration.

Acceptance checks include: availability and type/feature/capacity matching, preferred settings/save/reset and actual score, persisted draft snapshots, publication blocking and immutability, owned reads and forged filters, bounded/out-of-period weeks, retained locks, whole-transaction rollback on the second SQL insert, and a real Chromium → FastAPI → PostgreSQL generate/review/publish/student/instructor flow. Native publication confirmation, error retention and light/dark layouts at 1440/768/390 px are also exercised. Narrow week grids and tables scroll internally; mobile day view remains available.

No production migration, external deployment, environment publication or merge is part of implementation. Current-user validation/last-admin protections from the earlier security branches are retained. Existing deprecation warnings remain visible.

### Final recorded results

- `RUN_BROWSER_E2E=1` with a disposable PostgreSQL 16 DSN and installed Chromium: **116 passed, zero skipped**, in one completed full Python run.
- `BROWSER_EXECUTABLE_PATH=/usr/bin/chromium npm test`: all chained Node/browser checks passed, including the new planning UI suite. Those Node fixtures mock HTTP; the optional Python-launched browser test uses real HTTP, FastAPI and PostgreSQL.
- Verified cancellation performs no generation/settings/rule writes; UI errors retain settings; the timeout path returns JSON-safe structured 503 and preserves the old draft. All generated successful results report zero hard conflicts after full draft validation.
- `pip check`, Python/JavaScript syntax, Markdown local links and `git diff --check` passed.
- Existing crypt/passlib and FastAPI status-name deprecation warnings remain visible and were not suppressed.
- Temporary HTTP servers, private integration schemas and the disposable test container were removed. No existing university/application database was migrated, seeded or published.

### Actual test-app screenshots

These images come from real Chromium → FastAPI → disposable PostgreSQL with **synthetic test accounts/classes**, not real university records or mocked-rendered screenshots:

- [Admin draft, publication and filters](screenshots/sprint45-admin.png)
- [Student official timetable on mobile](screenshots/sprint45-student-mobile.png)
