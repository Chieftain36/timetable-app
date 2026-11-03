# UI/UX audit

Scope: actual `index.html` at `main` `69f906b`, 2026-11-03. Read-only review under the three repository skills. No redesign implemented. [Architecture and test limits](PROJECT_OVERVIEW.md); [feature matrix](CURRENT_FEATURES.md); [proposed work](DESIGN_ROADMAP.md).

## Evidence method

System `/usr/bin/chromium` driven by installed Playwright, loading the exact repository HTML at an intercepted synthetic origin. OpenAPI generated from imported app without lifespan startup. APIs, catalog rows, users and bearer tokens were synthetic fixtures. No real authentication, data or backend services used. All remaining network requests aborted.

Recorded light/dark empty-calendar renders at **1440×1000, 768×1000 and 390×1000** after layout transitions settled. Also rendered a populated synthetic Monday 09:00–10:00 session at all three widths, navigated all ten admin pages, and opened instructor/student shells. These are browser checks of frontend behavior, not live end-to-end acceptance. Six empty-state captures and three populated captures are temporary `/tmp/timetable-audit-*.png`; they are not shipped product screenshots or permanent repository artifacts.

## Browser-verified observations

| Area | Result | Meaning / limit |
| --- | --- | --- |
| Responsive shell | Document/body width equals viewport at 1440, 768, 390 in light/dark empty states; populated fixture also has no document horizontal overflow | No global overflow in tested cases. Does not prove large real tables or long-session density are usable. |
| Rendering | No page exceptions in admin/instructor/student shell checks; admin catalogs/generation/conflicts render | API fixtures only; a synthetic “no conflicts” response is not evidence real schedules are conflict-free. |
| Calendar | Fixture session renders; Next changes heading while same recurring session remains | Recurring-week UI, not date-specific schedule navigation. No server date request. |
| Search/filter | Zero `[data-calendar-action="filter"]` controls and zero `#live-filter-form` | Filtering helpers exist but are unreachable from ordinary calendar UI. |
| Role experience | Instructor/student see disabled My timetable and explicit unavailable message | No personal schedule flow. Instructor has no availability navigation item. |
| Mobile navigation | Opens and closes with Escape | Focus stays on external mobile-menu trigger after opening; Tab escapes sidebar marked `aria-modal=true`. Modal semantics do not match keyboard behavior. |
| Failure states | Synthetic `/time-slots` 503 produces unavailable-data panel and retry action; Courses still opens | Good distinction between failed and empty data; recovery retry success not exercised. |
| Empty state | No slots prompt links to slot manager; no scheduled classes list and disabled delete control | Useful starting point, but “Import time slots” opens a manager, not import. |
| Visual hierarchy | Restrained purple/neutral shell and explicit light/dark surfaces; primary controls readable in inspected captures | Not a measured contrast/WCAG certification. |

## Ranked issues

### U1 — P0: unsafe replacement workflow (source-confirmed, form browser-rendered)

`index.html:1383–1418` asks for UUID CSV lines, exposes a replace-all checkbox and immediately POSTs to the persistence endpoint. It does not ask for a second destructive confirmation, show affected session counts, provide a dry-run diff or disable the submit button while awaiting the request. `main.py:802` deletes all schedule records when replacement is selected. Transactionality protects rollback on failure, not intentional replacement or repeated user actions.

A reader sees “Generate timetable” as planning, but this writes the only current schedule. First improvement: truthful write warning, explicit replace confirmation and duplicate-submit prevention; versioned preview/approval requires backend work and separate authorization. Do not pretend a client confirmation creates rollback.

### U2 — P0: UI privacy statement conflicts with API authorization

`index.html:1757` says admin data is not exposed, but `main.py:881` accepts any authenticated caller and returns schedules without owner scope. The UI is not a privacy boundary. Fix server authorization before adding personal pages. Source plus synthetic ASGI role checks confirm the access gap; no personal records were accessed.

### U3 — P1: key role journeys are dead ends

Instructor availability renderer (`index.html:1002`, `1066`) and owner-only API exist, but `renderNavigation:1090` has only a disabled personal timetable entry for instructor users. Student group membership is absent. Users cannot finish their central job. Expose availability only after verifying the existing owner API flow; build personal schedule endpoints before enabling personal views.

### U4 — P1: calendar labels overstate data semantics

`stage2aSyncPage:433` says “draft or published”; neither status exists. “Sessions this week” counts all loaded recurring rows. Next/Previous advances dates without changing sessions; browser reproduced identical entries after advancing. Clarify “Recurring weekly template” until date-scoped schedule semantics are implemented. Include actual day/time in an accessible event description, not only grid position.

### U5 — P1: search/filter code is disconnected

`stage2aFilterMarkup:1513` and generated `chips` in `stage2aLiveCalendar` are never composed into its returned markup; filter handlers have no reachable opener. Backend ID filters do not solve UI discoverability. Connect labeled selectors/search to current state after approval, show active filters and clear action, and distinguish “no matches” from “no schedule”. Ensure exports/list and grid use consistent filtered rows; current list receives the unfiltered schedules array.

### U6 — P1: keyboard navigation and semantics

`stage2aToggleMobile:496` adds dialog/aria-modal without moving/trapping focus, making the background inert or restoring focus on close. Browser confirmed external trigger retains focus and Tab can escape. Escape works. `renderNavigation` replaces DOM buttons and `renderTimetable` replaces content, creating potential lost focus after navigation/settings. Calendar “Week/Day” uses selected styling without `aria-pressed`; settings checkbox text is a `<strong>` rather than an explicitly associated label. Native entity dialogs use names, explicit cancel and focus on opening, which is a sound foundation. Screen-reader output and focus-return behavior of every dialog remain unverified.

### U7 — P1: mobile calendar density

The 390 px calendar fits the page but still squeezes six day columns beside an 88 px time column. Empty headings fit; real descriptions, multiple events and instructor emails need density tests. Prefer a day agenda default on narrow screens with an explicit week alternative. No dense real-data or long-label usability pass was performed; do not report a measured clipping defect from an empty fixture.

### U8 — P1: uncertainty and write state handling differ across forms

Dialogs commonly show inline alerts and busy guards, while catalog creation/generation use global notices. Generator does not guard repeated submission. `renderConflicts:1684` replaces content with “Checking constraints…” and catches errors only via a global notice, leaving that loading message stale. Initial read failure isolation is good. Unify idle/loading/error/empty/success states without discarding entered data, and add cancellation or request-version checks to asynchronous render paths.

### U9 — P2: task hierarchy and labels

Shell title, secondary heading, large hero, three metrics, calendar and full schedule table compete for vertical space; at 390 px empty-state content extends well below the fold. Place the main scheduling task ahead of decorative metrics. “Import time slots” navigates to create/manage slots and should not imply a nonexistent import. Generation uses raw UUID CSV whereas manual scheduling and instructor creation already provide named selectors; reuse successful patterns. Raw IDs in class-list actions/error reports hinder resolution.

### U10 — P2: visual consistency and maintainability

`index.html` layers compiled Tailwind with several custom override blocks and generic catalog forms. Main calendar uses neutral/purple design tokens and supported dark mode; generation retains older utility-heavy markup and raw JSON output. Preserve the existing design direction but unify surfaces, headings, spacing, error messages and focus indicators. Measure actual contrast for text, disabled controls, focus rings and purple buttons before claiming accessibility. Do not add animation libraries just for decoration.

## Role-by-role flow and states

| Role/task | Current idle/empty | Loading/error | Success and missing next step |
| --- | --- | --- | --- |
| Admin catalogs | Empty rows and create form | Initial failure panels; validation messages and dialog alerts | Refreshes lists; large datasets, duplicate/missing dependencies need real integration checks. |
| Admin manual session | Add class; selectors | Dialog loads dependencies, retains inline errors, disables save while loading/saving | Refreshes calendar/list. Verify overlap errors and actual focus restoration on disposable data. |
| Admin generation | Required UUID text | Errors via notice; no guarded busy state | Raw JSON and success notice after persisted change; no review/draft/publish step. |
| Admin conflicts | Checking constraints | Notice on failure, stale loading content risk | Results table; no direct link to a relevant class or full policy certification. |
| Instructor availability | No reachable navigation | Renderer supports loading/errors and unsaved-change guard in source | API persists own matrix; normal entry point missing. |
| Student/instructor personal schedule | Explicit unavailable placeholder | No personal data fetch | No completed journey. |

## Source-only findings and unrun checks

JWT localStorage increases impact of an XSS; text escaping and `textContent` exist but this audit is not a comprehensive injection/security assessment. No drag/drop, persistent personalization or saveable filter views are implemented. No assistive-technology session, automated accessibility engine, contrast measurement, touch-target audit, reduced-motion exercise, slow-device performance run or real-database form submission was run. External design/component sites were not visually researched; earlier suggested Animata/Eldora/Aceternity/React Bits links are candidate references, not verified product dependencies or research evidence.

## First UI proposal — for approval only

**Direction A: Campus Workspace (recommended).** Keep vanilla JS, current light-first neutral surfaces and purple accent. Reduce hero/metric height, make weekly calendar or mobile agenda primary, add named filters and concise session details, align all forms/dialogs. Suggested tokens for review: background `#F7F7FB`, surface `#FFFFFF`, text `#1F2333`, muted `#596174`, accent `#6D3DF5`, border `#DDE1EA`; spacing 4/8/12/16/24 px; body 14–16 px; radii 8–12 px. These are proposals, not measured accessible palettes. Preserve the existing dark theme with independently tested token pairs.

**Direction B: Planning Console.** Denser desktop calendar, compact left context panel, restrained teal status accents and monospace time labels. Better for large scheduling teams; greater responsive complexity and risk of overloading casual users. Keep both options free of ornamental 3D and large motion effects on core work screens.

Start with safety/labels, accessible navigation and current role entry points; design cannot fix missing owner APIs or schedule lifecycle. No direction selected, code changed or assets installed. Approval is required before implementation.
