# Prioritized improvement roadmap

Audit baseline: `main` `69f906b`, 2026-11-03. **Proposal only: no implementation, migration, merge or deployment authorized by this document.** Scope and checks: [Project overview](PROJECT_OVERVIEW.md). Behavior inventory: [Current features](CURRENT_FEATURES.md). Interaction evidence: [UI/UX audit](UI_UX_AUDIT.md).

## Ranked backlog

Priority reflects privacy/data integrity and development risk before appearance. P0 items should gate broader rollout; P1 completes core workflows; P2 improves maintainability and scale. Risk describes the proposed change, not permission to execute it.

| Rank / priority | Improvement and bounded scope | Risk / dependencies | Acceptance criteria |
| --- | --- | --- | --- |
| 1 / P0 | Enforce schedule ownership server-side (`main.py:881`); define admin global access, instructor profile scope, student-group membership | High; product decision and reviewed schema migration for memberships. Existing global reads may need a separate explicit admin contract. | Student and instructor cannot read another person's rows even with forged query filters; missing profile/membership fails closed; admin retains intended global access; synthetic cross-role regression tests. |
| 2 / P0 | Make generation writes explicit; guard duplicate submissions and require replace-all confirmation; clearly separate future preview from current persistence | Medium for UI safeguards; high for lifecycle/backend redesign. Depends on approved action semantics. | Warning says existing records are replaced, affected count shown, Cancel sends no POST, one in-flight request, failed request retains inputs. No “preview” claim unless API truly has no persistence. No tests on production. |
| 3 / P0 | Restore real repository test sources and release evidence | Medium; retrieve original tests or author meaningful cases in a separate coding task; disposable PostgreSQL for integration. | `pytest` collects nonzero tests; every package test path exists; npm browser chain completes; failures/skips/counts reported; auth, ownership, manual conflicts, generation rollback and room rules exercised. CI proves intended suites ran. |
| 4 / P0 | Remove bootstrap-secret console disclosure (`start.ps1:26`) and correct README mismatch | Low; separate approved script/document change. | Running first/repeat setup does not print passwords, tokens or DB URLs; local secret files preserved; no secret in logs or test snapshots. Do not rotate credentials without approval. |
| 5 / P1 | Token lifecycle and admin-account protections | High; choose revocation/token-version or DB user checks, last-admin invariant and reauthentication policy. | Deleted/demoted users cannot keep previous authority beyond documented policy; cannot demote the final admin; race cases covered; existing valid sessions behave intentionally. |
| 6 / P1 | Enforce delivery-mode/room-type compatibility consistently in generation and manual writes | Medium/high; define lecture/practice/lab mapping, legacy unclassified-room policy and assignment linkage. | Lecture requirement cannot use practical-only room; insufficient compatible room yields clear structured reason; same policy across manual/edit/generation; duration/load/availability checks remain intact. |
| 7 / P1 | Expose instructor availability navigation and existing save flow | Low/medium; verify owner-only API and OpenAPI fallback first. | Active instructor can open own matrix, keyboard-cycle states, see limits/deadline, cancel unsaved departure and handle 409; student cannot open/write it; no admin impersonation. |
| 8 / P1 | Truthful recurring-week labels; define dated schedules before adding true weekly filtering | Low for text; high for semester/date schema. Depends on product recurrence decision. | Current UI says recurring template, no draft/published implication, counts explain scope. Future dates affect records only after explicit date model exists. |
| 9 / P1 | Connect existing filter controls; named selectors for generator assignments | Medium; preserve API payloads and route support detection. | Group/instructor/course/room filters reachable by keyboard, combined with AND, removable, clearable, no-matches state; grid/list share results; generator selects named records and still posts exact valid contract. |
| 10 / P1 | Fix mobile navigation and calendar keyboard/state semantics | Medium; decide modal dialog vs nonmodal drawer; test 390/768/1440. | Focus enters modal drawer, cannot escape it, Escape closes and restores trigger, background inert; or remove modal semantics with a correctly nonmodal design. Week/day selected state accessible; labeled toggles; visible focus. |
| 11 / P1 | Unified async state and accessible mobile agenda | Medium; UI proposal approval, preserve session data and callbacks. | Idle/loading/error/empty/success distinct; failed conflicts replaces stale spinner with retry; entered values survive failed writes; no duplicate mutations; mobile agenda retains course/day/time/group/instructor/room; dense/long-label fixture checks at target widths. |
| 12 / P1 | Define draft → validation → publish lifecycle, locks and version recovery as backend product work | High; depends on ownership, policy tests and approved migrations. These are absent features, not cosmetic changes. | Draft writes never mutate published schedules; hard conflicts block publication; published readers see authorized version; locked placements survive regeneration; restore is explicit, tested and permission-controlled. Split into separately reviewable stories. |
| 13 / P2 | Consolidate visual hierarchy, semantic tokens and compact catalog/table patterns | Medium; approve Campus Workspace or Planning Console first. | Calendar/task action appears before oversized decorative blocks; consistent titles/forms/dialogs; light/dark contrast measured; no global overflow at 1440/768/390; long labels, empty/error and keyboard cases verified. |
| 14 / P2 | Modularize frontend API/state/rendering and shared backend policies incrementally | Medium/high regression risk; baseline tests required. No React migration. | One module/domain per branch; unchanged route/payload/role behavior; escaped data rendering and focus preserved; equivalent regression fixtures pass. |
| 15 / P2 | Reproducible dependency/runtime and deployment documentation | Medium; preserve lockfiles during audit, change only in separate task. | Pin supported Python/Node and tested FastAPI minimum, commit appropriate locks, verify clean install and CSS output in disposable checkout, restore missing upgrade docs. Existing-volume update procedure explicit. |
| 16 / P2 | Requests, notifications, exports, versions/history, room availability and coordinator reporting | High cumulative scope; prioritize after core lifecycle. | Each approved feature has entity/permission/API/UI and meaningful acceptance test; do not equate toast with notification, creation timestamp with history, or generic GET filter with personal timetable. |

## Recommended sequence and review gates

1. Approve this audit and safety scope. First implementation branch: remove secret printing and misleading lifecycle language; restore trustworthy tests in a separate branch. Ownership and generation safety are rollout blockers and should proceed before richer personal views.
2. Specify role scope, memberships, token lifecycle and room compatibility. Agree on backward compatibility and disposable test strategy before any schema changes.
3. Repair current flows: instructor availability entry point, generator selectors/confirmation, connected filters, error retry and mobile focus. Keep diffs small and reviewable.
4. Approve one visual direction. Recommended first UI slice: calendar hierarchy/labels plus a mobile agenda, preserving existing API calls and controls. Do not start a broad restyle before approval.
5. Build persistent drafts/publication and later sprint features as explicit backend-and-UI stories, with migrations and rollback reviewed separately.

A safe UI confirmation does not solve publication/versioning. Conversely, waiting for all lifecycle features should not block correcting current authorization, exposed passwords or unavailable regression tests.

## Verification contract for subsequent approved work

| Scenario | Required result |
| --- | --- |
| Privacy | Student own group only; instructor own assignments only; no cross-scope access via optional filters or direct API call. |
| Catalog/manual schedule | Reference validation; inactive dependency and Sunday rejection; capacity/type/duration rules; own-record edit does not conflict with itself. |
| Overlaps | Room/instructor/group same and partially overlapping periods reject; adjacent intervals accepted; independent resources behave according to agreed slot-template policy. |
| Availability | Blocked slots never used; no available slots produces explicit infeasibility; preferred slots never override hard rules; deadline/save failure preserves form state. |
| Generation | Insufficient rooms/slots and excess workload fail safely; no partial persisted result on failure; replacement has confirmation and tested rollback; no duplicate POST. |
| Concurrency | Two writers attempting conflicting placements do not both commit; verify advisory-lock and database behavior on disposable PostgreSQL. |
| Browser/accessibility | 1440/768/390, light/dark, dense/long data; keyboard dialogs/drawer/navigation, live errors and focus return; measure contrast, do not infer it visually. |
| Tooling | Nonzero Python tests, actually executable npm test scripts, explicit integration skips, clean tracked diff, no secrets in artifacts. |

## Design directions awaiting approval

- **Campus Workspace (recommended):** preserve purple/neutral identity, compact task-first calendar, named selectors and mobile day agenda; least disruption.
- **Planning Console:** denser desktop planning surface with stronger data hierarchy, useful for experienced schedulers; higher mobile complexity.

Full initial token/interaction proposal is in [UI/UX audit](UI_UX_AUDIT.md#first-ui-proposal--for-approval-only). No external component library selected or installed. Reference sites have not been visually verified in this audit; do not claim their code is integrated or licensed for this product.

## Delivery and stopping point

Exactly four new Markdown files on `audit/architecture-features-ux`; application, SQL, tests and deployment unchanged from audited main. Existing runtime processes, data, secrets and environment configuration untouched. Branch may be reviewed as documentation only. No merge, deployment, environment publication or UI implementation performed. Await user approval for the next scoped change.
