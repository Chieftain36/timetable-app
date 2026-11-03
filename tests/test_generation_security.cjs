const {chromium} = require('./browser.cjs');
const fs = require('fs');
const assert = require('assert');

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(10000);
    const errors = [], writes = [];
    let release;
    let fail = true;
    async function waitForRelease() {
      const deadline = Date.now() + 10000;
      while (!release && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 10));
      assert(release, 'Generation POST did not arrive');
    }
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const request = route.request(), path = new URL(request.url()).pathname;
      const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
      if (path === '/') return route.fulfill({contentType: 'text/html', body: fs.readFileSync('index.html', 'utf8')});
      if (path === '/api/v1/schedules/generate') {
        writes.push(request.postDataJSON());
        await new Promise(resolve => { release = resolve; });
        return fail ? json({detail: 'Synthetic failure'}, 500) : json({generated_count: 1, entries: []}, 201);
      }
      const fixtures = {
        '/api/v1/time-slots': [],
        '/api/v1/courses': [{id: 'c1', is_active: true}],
        '/api/v1/student-groups': [{id: 'g1', is_active: true}],
        '/api/v1/instructors': [{id: 'i1', is_active: true}],
        '/api/v1/schedules': [{id: 's1'}, {id: 's2'}],
      };
      if (fixtures[path]) return json(fixtures[path]);
      return json({}, 404);
    });
    await page.goto('http://app.test/');
    await page.evaluate(() => {
      localStorage.setItem(TOKEN_KEY, 'synthetic-token'); tokenPayload = {role: 'admin'};
      document.getElementById('login-view').classList.add('hidden');
      document.getElementById('app-view').classList.remove('hidden');
      activeView = 'generate'; renderNavigation(); renderGenerateView();
    });
    const form = page.locator('#generate-form');
    await form.locator('[name=assignments]').fill('c1,g1,i1,1');
    await form.locator('[name=replace_existing]').check();
    page.once('dialog', async dialog => {
      assert(dialog.message().includes('2 current schedule entries'));
      assert(dialog.message().includes('no undo'));
      await dialog.dismiss();
    });
    await form.locator('button').click();
    await page.waitForFunction(() => !document.getElementById('generate-form').dataset.busy);
    assert.equal(writes.length, 0, 'Cancel must not generate');

    page.once('dialog', dialog => dialog.accept());
    await form.locator('button').click();
    await page.waitForFunction(() => document.getElementById('generate-form').dataset.busy === '1');
    await waitForRelease();
    assert(await form.locator('button').isDisabled());
    await form.evaluate(element => element.requestSubmit());
    await form.locator('[name=replace_existing]').uncheck();
    assert.equal(writes.length, 1, 'Repeated submit must not send another POST');
    assert.equal(writes[0].replace_existing, true, 'Use the confirmed replacement option');
    release();
    await page.waitForFunction(() => !document.getElementById('generate-form').dataset.busy);
    assert.equal(await form.locator('[name=assignments]').inputValue(), 'c1,g1,i1,1');
    assert((await page.locator('#notice').innerText()).includes('Server error'));
    assert(await form.locator('button').isEnabled());

    fail = false; release = null;
    await form.locator('button').click();
    await waitForRelease();
    assert.equal(writes.length, 2);
    assert.equal(writes[1].replace_existing, false);
    release();
    await page.waitForFunction(() => !document.getElementById('generate-form').dataset.busy);
    assert((await page.locator('#notice').innerText()).includes('1 schedule entries generated'));
    for (const width of [1440, 768, 390]) {
      await page.setViewportSize({width, height: 900});
      for (const theme of ['light', 'dark']) {
        await page.evaluate(theme => stage2aApplyTheme(theme), theme);
        await page.waitForTimeout(400);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${width} ${theme}`);
      }
    }
    assert.deepEqual(errors, []);
    console.log('PASS: replacement warning/count, cancellation, double-submit guard, confirmed payload, failed-save retention/retry, responsive light/dark generation form (mock API only).');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
