# Current features: evidence-based status

Baseline/date/scope and exact checks: [Project overview](PROJECT_OVERVIEW.md). Status is for repository `main` at `69f906b`, not uploaded alternative builds. No percentages inferred from grades, report claims or story-point totals.

**Implemented** = executable source path exists; does not mean live-DB verified. **Partial** = meaningful portion exists but acceptance flow has a gap. **Planned** = missing in this repository; may appear in backlog. **Not verified** = evidence unavailable. Browser evidence below always uses synthetic intercepted APIs; DB-dependent behavior remains not verified unless explicitly stated otherwise.

## Role journeys

| Role | Available journey | Main gap |
| --- | --- | --- |
| Admin | Login → recurring weekly calendar → catalogs/teaching loads → manual sessions → conflict check or direct generation | Generation requires raw UUID lines; no preview/version/publish lifecycle. |
| Instructor | Login → “Personal timetable not yet available” | Own availability backend and renderer exist but navigation never exposes availability. Personal timetable absent; general API is overbroad. |
| Student | Login → same placeholder, disabled My timetable item | No student-group membership or server-scoped personal read. |
| Department coordinator | No dedicated role | `UserRole` allows only admin, instructor, student. Workload API is admin/owner, not coordinator-specific. |

## Backlog matrix (US1–35)

Story numbering follows the supplied Excel plan. Sprint 3 report swaps room/instructor numbering and changes estimates; use story behavior, not conflicting report labels, for verification.

| US | Feature / role | Status | Repository evidence and limitation |
| --- | --- | --- | --- |
| 1 | Email/password login / all | Implemented | `main.py:276`, `auth.py:29`, `index.html:1818` login submit. Native labels, show-password, error states. Real credentials login not run. |
| 2 | Accounts/roles / admin | Implemented, security gaps | `main.py:296–363`, `index.html:1237` user dialog. No last-admin demotion guard; JWT role changes not immediately effective. |
| 3 | Role-specific dashboard / all | Partial | `index.html:1090` navigation and `1755` role gate. Admin workspace exists; instructor/student placeholder browser-confirmed. |
| 4 | Course CRUD / admin | Implemented | `main.py:366–426`, `index.html:1281` catalog dialog; forms render. DB writes not tested. |
| 5 | Room CRUD/capacity / admin | Implemented | `main.py:429–492`, `models.py:160`, `index.html:1419` room selectors. Practical 5–25; lecture 1–70 currently enforced. Older README section still says 100. |
| 6 | Student groups/year / admin | Implemented | `main.py:495–550`, `models.py` group year/code validators, `schema.sql:107–109`, `index.html:1318`. Format validation is not semantic adjacent-year validation. |
| 7 | Instructor profiles/status / admin | Implemented | `main.py:553–642`, status route at 1043, `index.html` instructor picker and status actions. Active profile ownership enforced for availability. |
| 8 | Weekly slots / admin | Implemented | `main.py:645–720`, `_validate_slot`, `schema.sql:143–150`, `index.html:1148` slot library and bulk builder. Mon–Sat; no overlapping weekday slot definitions. Bulk UI is not evidence of atomic batch API. |
| 9 | Course/group/instructor teaching loads / admin | Implemented | `main.py:1072–1131`, `models.py` TeachingLoadCreate, `schema.sql:154`, `index.html` renderLoads. Duration and load validation; persisted loads can feed generator if assignments omitted. |
| 10 | Own availability / instructor | Partial | Owner-only routes `main.py:971–1039`, matrix at `index.html:1017`, preferred/blocked/deadline validation. `renderNavigation:1090` never adds availability; ordinary instructor journey cannot open it. |
| 11 | Manual session create / admin | Implemented | `main.py:1166–1205`, `index.html:1454` labeled selectors and inline errors. Does not enforce delivery-mode fit or matching stored teaching load. |
| 12 | Edit/delete sessions / admin | Implemented | `main.py:838–879`, `1208`, `index.html:1454`, `1591–1682` list, edit and batch-delete confirmation. DB write correctness not reverified. |
| 13 | Room double booking prevention | Implemented | Interval check in `_validate_manual_schedule`, solver `_entry_conflicts_with_assignments`, `schema.sql:65`; pure detector partial-overlap example passed. Concurrency/DB checks unrun. |
| 14 | Instructor double booking prevention | Implemented | Same paths, instructor resource key; `schema.sql:66`, detector enumerates instructor overlaps. PostgreSQL concurrency not verified. |
| 15 | Group double booking prevention | Implemented | Same paths, group resource key; `schema.sql:67`. PostgreSQL concurrency not verified. |
| 16 | Automatic generation / admin | Implemented, bounded greedy behavior | `main.py:746–828`, `scheduler.py:98–280`, `index.html:1383`. Deterministic placement; 422 for unscheduled requirements; success writes records. No full solver completeness guarantee. |
| 17 | Availability-aware generation | Implemented | Solver preference map and unavailable-slot exclusion (`scheduler.py:123`, `230` vicinity); synthetic blocked-only slot rejected. No real dataset evidence. |
| 18 | Room capacity/type-aware generation | Partial | `scheduler.py` eligible_rooms checks only capacity/activity; delivery_mode absent from requirement snapshot. Synthetic lecture placed in practical-only room. Capacity rejection passed. |
| 19 | Generation preferences / admin | Partial | Instructor “preferred” slots influence solver order; no admin minimize-gaps/avoid-early settings model, save UI, global objective or quality score. These are different from instructor availability preferences. |
| 20 | Regenerate safe draft / admin | Partial | `replace_existing` regenerates persisted schedules, transaction intended to replace all rows. No durable draft, locks, version or published-state protection. |
| 21 | Weekly timetable / user | Partial | `index.html:1517–1589` renders recurring week/day grid; synthetic rows verified. Week headings advance while same sessions repeat. No date-specific weeks; non-admin UI unavailable. |
| 22 | Student own-group timetable | Planned (missing) | No membership entity in schema, no owner-scoped route; `index.html:1757` placeholder. General GET is **not** a personal timetable. |
| 23 | Instructor own timetable | Planned (missing) | Profile relation exists, but no schedule predicate derived from authenticated instructor. Same placeholder. |
| 24 | Timetable search/multiple filters | Partial | Backend GET accepts optional group/room/instructor IDs; client has query and AND-filter logic (`index.html:1513–1530`). Filter markup and chips not inserted in returned calendar, no reachable filter button/form. No course backend filter or ordinary search control. |
| 25 | Publish official timetable / admin | Planned (missing) | No status/publish field, table or endpoint. “draft or published” UI text has no corresponding lifecycle. |
| 26 | Instructor change request | Planned (missing) | No request entity/route/UI. |
| 27 | Admin request review | Planned (missing) | No approval/rejection workflow. |
| 28 | Change notifications | Planned (missing) | Notice/toast UI is transient feedback, not persistent user notifications. |
| 29 | Session locks | Planned (missing) | No lock flag or protected-placement logic; full replacement does not preserve session IDs. |
| 30 | Save/restore versions | Planned (missing) | No version entity, snapshot, restore endpoint or UI. |
| 31 | PDF/Excel export | Planned (missing) | No export route or client download/print workflow for timetables. |
| 32 | Room availability view / admin | Partial | GET room filter exists; calendar/list show rooms. No dedicated free/busy room timetable or availability UI. |
| 33 | Instructor workload summary / coordinator | Partial | `main.py:942`, `1048` planned/actual minutes and limits; coordinator role absent, no dedicated summary view. |
| 34 | Basic statistics / admin | Partial | `index.html:1544` schedule count/unique rooms; slots count helper. No authoritative aggregate endpoint or full statistics dashboard. “This week” count is all recurring loaded rows. |
| 35 | Timetable change history / admin | Planned (missing) | Creation timestamps are not actor-attributed before/after history. No audit-event entity/route/UI. |

## Important implementation boundaries

- Conflict checker reports resource overlaps and room capacity only (`scheduler.py:278`). It does not certify workload, instructor availability, room type or publication readiness.
- Manual and generated paths independently implement policies; room compatibility and assignment/duration checks differ. A shared contract and regression fixtures are higher priority than decoration.
- The database's global exclusion of overlapping time slots limits templates; it is stronger than checking shared resource conflicts alone.
- Non-admin UI hiding does not protect data: authenticated users can request all joined schedule rows (`main.py:881`), including instructor email/group/room details. Optional caller-provided filters cannot enforce ownership.
- No documented “quality score”, AI model, drag-and-drop placement, publish workflow or persistent draft should be advertised as delivered.

## Verification inventory

Browser checked synthetic admin navigation for users/courses/rooms/groups/instructors/slots/loads/generate/conflicts; instructor/student show placeholder and disabled personal navigation. Pure solver/ASGI checks verify only selected branches. No production or real PostgreSQL test was run. Repository tests are missing, so **none of the source-implemented stories meets a verified automated-test Definition of Done based on this checkout alone**. Restore tests before using prior report pass counts as release evidence.
