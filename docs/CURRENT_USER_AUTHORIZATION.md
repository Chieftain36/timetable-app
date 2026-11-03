# Current-user authorization and last-administrator protection

Branch: `fix/current-user-authorization`, based on `d45c676` (security safeguards and restored tests). This is a scoped backend security change, not a redesign or deployment.

## Changes

1. Bearer JWTs still use HS256 and the existing expiry policy. Validation now requires both `exp` and `iat` claims, which the application's login already issues.
2. Every protected request loads the authenticated account by its token subject. If the account was deleted, or its persisted role/email no longer matches the signed identity, the API responds with 401 and a Bearer challenge. The existing frontend handles 401 by signing out; a changed account must authenticate again before using its current permissions.
3. A PATCH that would demote the last administrator returns 409 and changes nothing. Demotion remains possible when a different admin account exists. Existing admin deletion restrictions and instructor-profile restrictions remain unchanged.
4. Request connection/transaction logic moved unchanged from `main.py` into `database.py`, shared by auth and handlers. `main.get_connection` remains a re-export of the same callable, so existing test overrides remain valid. FastAPI shares that dependency within each request. Domain writes still take the existing transaction-scoped advisory lock; the current-account check now executes inside that same boundary before the handler, preventing a queued write from relying on a role changed by the previous writer.

No schema, deployment, secret, password, environment configuration or production database changes. Login payload, response shape, route paths, query parameters and role names are preserved. Formerly accepted JWTs missing required time claims or carrying outdated identity intentionally fail closed.

## What this does not provide

This is **current-account validation**, not a permanent token blacklist. If an account's role/email later returns to its old value before the old token expires, that token can match again. There is no per-session revoke/logout-all endpoint or token-version column. Password-only changes do not revoke previously issued tokens in this change. A dedicated session/version policy remains future work.

An in-flight read authenticated before an account update may complete; subsequent protected requests observe committed state. Write checks and domain writes share the cooperating application's advisory lock. Clients writing directly to PostgreSQL outside that lock remain outside this application-level concurrency contract.

A database outage now prevents valid users from completing protected requests (503 if no pool is available). Invalid/missing credentials are rejected before a DB connection is acquired. This deliberately prefers authorization correctness to continuing with stale signed roles.

## Tests and adapter accuracy

`test_auth_lifecycle.py` covers:

- both student/instructor demotion attempts against the final admin;
- allowed demotion when another admin exists, then rejection of the old admin token for reads and writes;
- deletion, email change, promotion and reissued current-role tokens;
- missing `exp`/`iat` claims;
- invalid credentials when no DB pool exists;
- real concurrent admin demotions through the production connection/transaction dependency.

The live test runs two simultaneous self-demotions against synthetic accounts in a unique private schema of a disposable PostgreSQL 16 database. Expected results are one 200 and one 409, exactly one remaining administrator, and 401 for the successfully demoted account's old token. Pool, overrides and schema are restored/removed in `finally`.

The stateful fake adapter in `test_app.py` was updated to honor SELECT projections, UPDATE RETURNING projections and the exclusion predicate used to find another admin. It no longer incorrectly returns password columns when production SQL only requests public identity fields. Security fixtures now represent persisted accounts instead of treating arbitrary signed claims as accounts. The live ownership fixture uses matching real identity claims and includes explicit synthetic admin/student users.

See [safe test setup](SECURITY_AND_TESTS.md#run-safely) before setting `SPRINT2_TEST_DSN`. Never connect tests to university/production data. Browser tests are not real backend integration. This change does not modify the UI; authorization, real SQL and transaction checks are the primary validation targets.

## Rollback and remaining scope

Revert this commit to restore the prior dependencies; no schema rollback is required. That also removes the new current-account authorization and last-admin protection. Do not merge, deploy or alter account credentials as an implicit part of review.

Room delivery-mode validation, instructor availability navigation, personal student membership, drafts/publication and design work remain separate audit items.

## Verification outcome

- Full Python suite on disposable PostgreSQL 16: **105 passed, zero skipped**, in one completed run. Existing crypt/passlib and FastAPI status-name deprecation warnings remain visible.
- Real SQL concurrency and owner-scoped schedule reads also passed their focused checks before the full regression run.
- Existing OpenAPI route paths, methods, query parameters, request bodies, response definitions and security requirements match the baseline schema exactly. Intentional behavioral changes are the 401/409 safeguards described above.
- `pip check`, Python source parsing, documentation link checks and `git diff --check` passed.
- No browser suite rerun for this backend-only follow-up: UI source is unchanged; prior browser evidence is recorded separately in `SECURITY_AND_TESTS.md`.
- Only the new disposable `timetable-auth-test-db` container on loopback port 55432 was used. It and the test schemas were removed after verification. No existing application database, schema, volume or credentials were modified.
