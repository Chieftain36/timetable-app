# University Timetable — Version 2.5

Complete FastAPI + Pydantic v2 + PostgreSQL application with an English interface.
This archive contains separate source files, tests, schema and launch scripts.

## Existing installation: read this first

**Version 2.5 requires a database schema update.** Replacing only index.html is
not sufficient. Follow UPDATE-V2.5.md. Keep your existing .env and database.
Do not use the older version-specific update instructions from previous releases.

## First installation

Start Docker Desktop with Linux containers, extract this archive, and open
PowerShell inside timetable-app:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

Open http://localhost:8000. On a first installation, start.ps1 creates a
private .env with generated database, JWT and bootstrap administrator credentials.
If .env already exists, the script preserves it unchanged. The script does not
print passwords or other secrets. For initial administrator login, view the local
.env privately (never paste, share, commit or screenshot its values). Do not
assume that an existing or complete project archive is safe to share.
The first Docker build needs internet; the site's styles are embedded locally.
API documentation: http://localhost:8000/docs.

## Local secrets and safe Git preparation

The real .env is private and must never be added to Git or sent with a project
archive. .env.example contains dummy placeholders only. The .gitignore excludes
.env, database dumps, old index.before-*.html copies, and sensitive archives.
The .dockerignore keeps these out of the Docker build context; keep the existing
docs and tests exclusions as well. Inspect Git's staged file list before the
first commit and do not publish secrets, dumps, tokens or credential screenshots.

WARNING: Complete project archives can contain real .env files, database dumps,
credentials, or earlier backups even if .gitignore and .dockerignore are present.
An older archive of this project includes such private files. Treat all existing
project archives as sensitive until their contents have been inspected. Do not
upload or commit archives containing credentials or university records. Build
a separate sanitized source-only archive with private files excluded.

This setup uses PostgreSQL 16 in Docker Compose with a persistent named volume
(timetable-data). Do not remove or recreate that volume during Git preparation.
Changing .env alone does not rotate the password inside an initialized database.
Use the separately reviewed credential-rotation procedure only after approval.


## Rooms

Select Room type, Building and Room number; enter Capacity.

- Practical: blocks D–I, floors 1–3, rooms 01–12 on each floor.
  Examples: D101–D112, D201–D212, D301–D312, through I312.
  Capacity: a whole number from 5 to 25 inclusive.
- Lecture: blocks A–D, floors 1–2. Valid names: A1, A2, B1, B2, C1, C2, D1, D2.
  Capacity: a whole number from 1 to 100 inclusive.

The API and PostgreSQL enforce these combinations. Existing unclassified rooms
remain stored and usable; migration does not rename them or change capacities.
To reclassify an old room, PATCH /api/v1/rooms/{id} with room_type, name, building
and a valid capacity. Changes cannot invalidate its existing scheduled classes.
Groups still require 5–50 students. A practical room cannot hold a group larger
than its capacity; such an assignment is rejected.

## Accounts and instructor availability

Create users in Users. To add an instructor profile, open Instructors, select the
existing instructor account by email and enter its department. UUIDs are selected
automatically. Swagger Example Value contains sample IDs, not database records.
Users has Edit/Delete actions; admin accounts cannot be deleted. Linked profiles
and scheduled classes may restrict other changes. Instructors can be deactivated
with confirmation and reactivated using Activate.

Sign in with an active instructor account and open My availability. Click a slot
to cycle Available → Unavailable → Preferred and save. Both manual scheduling and
automatic generation reject unavailable slots. Deadline, unavailable-slot limits
and workload constraints apply. Already scheduled slots cannot be marked blocked
until the administrator moves or removes those classes.

## Manual scheduling

Open Weekly timetable and click Add class. Select the course, group, instructor,
room and time slot, then save. The chosen entities must be active.

Each class has Edit and Delete buttons. Editing pre-fills the form and updates
that single class; it does not regenerate the timetable. Deletion requires
confirmation; batch deletion is also available. Lists and the weekly grid update
without a full page reload.

Saving rejects:
- Room, instructor or group overlaps, including partial interval overlaps.
- Instructor unavailable slots, inactive entities and Sunday slots.
- Insufficient room capacity.
- Instructor workload or course/group curriculum limits.

Failures keep the entered values and the existing schedule unchanged. Concurrent
writes are serialized in PostgreSQL. Conflict check reports room/instructor/group
conflicts. Automatic generation remains available and respects availability.

## API

POST /api/v1/schedules and PATCH /api/v1/schedules/{id} are admin-only.
POST requires course_id, group_id, instructor_id, room_id and time_slot_id (UUIDs).
PATCH accepts one or more of those fields. Both return the saved IDs, id and
created_at; GET /api/v1/schedules returns the expanded display fields.
Conflicts return 409, invalid input 422 and missing records 404.

Room input includes room_type (practical or lecture; default practical), name,
building, capacity and is_active. Building must match the name's first letter.

## Verification and development

```powershell
pip install -r requirements.txt
pytest -q
npm install
npx playwright install chromium
npm test
```

Set SPRINT2_TEST_DSN to a separate test PostgreSQL database to enable live tests.
The account needs schema/extension creation privileges. Tests create and drop only
their own schemas. Without this variable, two integration tests are skipped.
BROWSER_CHANNEL=msedge selects installed Edge instead of downloaded Chromium.

Version 2.5 passed 80 pytest cases including real PostgreSQL 18 migration,
concurrent scheduling and database room constraints, plus browser tests for
room selectors, manual class creation/editing and the existing features.

After adding Tailwind utility classes, run npm run build:css to update embedded
styles. Node is needed for development/tests only, not for running the app.
docs/UserStories.xlsx remains the unchanged reference workbook.

## Troubleshooting

If localhost refuses a connection, the server is not accepting requests. Check:

```powershell
docker compose ps
docker compose logs --tail=100 app db
```

Replacing files does not start the server. Keep Docker Desktop running.
Use docker compose down to stop while retaining the data volume; never use
`down -v` unless you intend to delete that database.

## Version 2.5

Courses, Rooms and Student groups now have Edit and Delete actions with confirmation and inline server errors. Referenced records cannot be deleted. Room forms default to 25 seats for practical rooms (5�25 allowed) and 70 for lecture rooms (1�70 allowed). Existing values are preserved when opening Edit. Unclassified legacy rooms require an explicit type selection. Build class grid is inside Add a time slot, before the slot library. Session changes clear stale notices.

## Security and restored regression checks

See [Security safeguards and test setup](docs/SECURITY_AND_TESTS.md) for the
restored test sources and safe disposable-database instructions. Schedule reads
are admin-wide or scoped to an active instructor's own profile. Students cannot
read schedules until authorized group membership is implemented. Generation
saves immediately; replacing entries requires confirmation and has no undo.
The UI does not implement persistent draft/publication states.

Protected requests also verify the current persisted account identity. Deleted
accounts and tokens whose role/email no longer matches return 401; the last
administrator cannot be demoted. See [authorization lifecycle](docs/CURRENT_USER_AUTHORIZATION.md)
for session-revocation limits and concurrency tests.
