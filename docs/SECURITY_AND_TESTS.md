# Security safeguards and restored regression tests

Branch: `fix/security-and-regression-tests`, based on audit commit `698717f`.

## Scope

- `GET /api/v1/schedules`: admin retains global access and optional filters. An active instructor is restricted to the profile derived from the authenticated user; a different requested instructor returns 403. Students return 403 even with a group filter because no authorized student-group membership exists yet. This intentionally fails closed; it does not implement the missing student personal-timetable feature or enable personal UI.
- `start.ps1` no longer reads credentials back into the console. Existing `.env` creation and preservation behavior is retained.
- Generation says it saves immediately and has no preview, version or undo. Replacement fetches the current schedule count, requests explicit confirmation and warns the count can change. Cancellation sends no generation POST. The form prevents concurrent repeat submissions, keeps the confirmed replacement option, checks for a changed session, and restores controls after errors while retaining entered text.
- Weekly page copy now describes recurring sessions without claiming draft/publication state. No redesign, schema change, production migration, dependency upgrade or deployed-data operation.

## Restored test provenance

Recovered `test_app.py`, `test_manual.py`, `tests/fixtures/uploaded_schema.sql` and the npm test sources from the user-supplied source archive under `merge-review-20261031/timetable-app`. Application files from that archive were **not** copied into this repository.

Updated outdated browser expectations to match current functionality: slot window/end/duration preview rather than removed count input; current creation success text; shell identity helper; browser-normalized time input; day selection before manual time selection; one integrated bulk slot form without a duplicate creation form. Assertions still verify the underlying writes, overlap validation, error retention and results. No failing assertions disabled.

`tests/browser.cjs` defaults to Playwright-managed Chromium and optionally accepts `BROWSER_EXECUTABLE_PATH` for an installed browser; existing `BROWSER_CHANNEL` callers remain supported. `tests/frontend.js` is a generated extraction for Node checks and is ignored. The existing package test chain is restored and extended with `tests/test_generation_security.cjs`.

New `test_security.py` checks student fail-closed behavior, instructor ownership and forged filters, missing/inactive profiles, retained admin access, missing/invalid JWTs, replacement rollback through a stateful fake DB, and actual owner-scoped joined SQL on disposable PostgreSQL.

## Run safely

Python (no database):

```sh
python -m pytest -q
```

Unset `SPRINT2_TEST_DSN` if you want **no** database operations. Integration tests skip without it. Only set this variable to an explicitly disposable test database: tests create/drop private schemas and install extensions. Never point it at university/production data. The new live ownership test uses synthetic rows and a unique private schema and removes that schema afterward.

Browser checks:

```sh
npm test
```

Install a Playwright Chromium build first if needed (`npx playwright install chromium`), or select an existing compatible browser:

```sh
BROWSER_EXECUTABLE_PATH=/usr/bin/chromium npm test
```

PowerShell equivalent:

```powershell
$env:BROWSER_EXECUTABLE_PATH = 'C:\path\to\chrome.exe'
npm test
```

Browser tests use intercepted synthetic HTTP responses, not a production server. They exercise actual frontend code but are not live backend integration. `npm run build:css` rewrites `index.html` and is not required to run these tests.

## Remaining limits and follow-up

- JWT role claims still last until expiry; immediate revocation and last-admin protections remain a separate P1 change. No account/credential rotation was performed.
- Delivery-mode/room-type compatibility, instructor availability navigation, publication/drafts/versioning and mobile focus issues remain as recorded in the audit.
- The generation confirmation is a safeguard, not a server-enforced optimistic concurrency check or recovery mechanism. Another admin can change data after the displayed count is read.
- PostgreSQL verification must distinguish the real integration tests from fake transaction tests. Fake rollback confirms application control flow, not database guarantees by itself.
- PowerShell is unavailable on this Linux host; the startup output change was source-reviewed, not executed under Windows.
- Existing passlib/Python crypt and FastAPI status-name deprecation warnings are not suppressed by this change.

No merge, deployment or environment publication is part of this branch. Reverting the branch restores prior behavior; no data migration or secret changes need reversal. Audit documents describe the earlier baseline and remain historical evidence rather than being rewritten as if these fixes had already existed.

## Verification in this change

- Full Python run with a disposable PostgreSQL 16 container: **93 passed**, no skips. The new live ownership test was added afterward and run separately: **1 passed**. Thus all 94 collected cases were exercised across these two runs; this is not a claim of a single 94-case run.
- Restored baseline without a database: 78 passed, 2 integration cases skipped before adding security cases. New synthetic authorization checks: 12 passed; replacement fake-transaction rollback included in the full run.
- `BROWSER_EXECUTABLE_PATH=/usr/bin/chromium npm test`: all ten chained check/test scripts after preparation passed, including the new generation safeguards. Browser HTTP responses were mocked. Generation form checked at 1440/768/390 px in light and dark themes.
- `.venv/bin/python -m pip check`: passed. `git diff --check`: passed. PowerShell startup not executable on this host.
- PostgreSQL was a new `--rm` container named `timetable-security-test-db`, bound only to loopback port 55432, using synthetic users/data. Existing application databases and volumes were not used. Private integration schemas were cleaned up; the disposable container was removed after validation.
