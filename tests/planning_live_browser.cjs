// Optional real HTTP + PostgreSQL acceptance test, launched by test_planning.py.
const {chromium}=require('./browser.cjs');const assert=require('assert');
(async()=>{const browser=await chromium.launch({headless:true});try{
 const base=process.env.PLANNING_TEST_BASE,headers=JSON.parse(process.env.PLANNING_TEST_HEADERS);
 async function open(role){const context=await browser.newContext({viewport:{width:1440,height:1000}});const page=await context.newPage();page.setDefaultTimeout(15000);const faults=[];page.on('pageerror',e=>faults.push(e.message));await context.addInitScript(token=>localStorage.setItem('university-timetable-token',token),headers[role].Authorization.slice(7));await page.goto(base);await page.locator('#plan-list').waitFor();return {context,page,faults};}
 const admin=await open('admin');const page=admin.page;
 await page.locator('#navigation [data-view=generate]').click();await page.locator('#plan-run').waitFor();
 page.once('dialog',d=>d.accept());await page.locator('#plan-run').click();await page.locator('#planning-notice').filter({hasText:'Quality score:'}).waitFor();
 await page.locator('#plan-review').click();await page.locator('#plan-list tbody tr').nth(3).waitFor();assert.equal(await page.locator('#plan-list tbody tr').count(),4);
 await page.locator('#plan-publish').click();await page.locator('#planning-publish-dialog [data-confirm]').click();await page.locator('#planning-publish-dialog').waitFor({state:'detached'});await page.locator('#planning-notice').filter({hasText:'published'}).waitFor();
 await page.screenshot({path:'/tmp/planning-live-admin.png',fullPage:true});assert.deepEqual(admin.faults,[]);
 for(const role of ['student','teacher']){const view=await open(role);assert.equal(await view.page.locator('#plan-list tbody tr').count(),2);assert(!(await view.page.locator('#plan-list').innerText()).includes('GR-1'));await view.page.setViewportSize({width:390,height:900});await view.page.waitForTimeout(450);assert(await view.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await view.page.screenshot({path:`/tmp/planning-live-${role}.png`,fullPage:true});assert.deepEqual(view.faults,[]);await view.context.close();}
 await admin.context.close();console.log('PASS real browser → FastAPI → PostgreSQL: generate, review, publish, authorized student/instructor views and mobile layout. Synthetic test accounts only.');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
