// Optional real HTTP + PostgreSQL acceptance test, launched by test_planning.py.
const {chromium}=require('./browser.cjs');const assert=require('assert');
(async()=>{const browser=await chromium.launch({headless:true});try{
 const base=process.env.PLANNING_TEST_BASE,headers=JSON.parse(process.env.PLANNING_TEST_HEADERS);
 async function open(role){const context=await browser.newContext({viewport:{width:1440,height:1000}});const page=await context.newPage();page.setDefaultTimeout(15000);const faults=[];page.on('pageerror',e=>faults.push(e.message));await page.goto(base);await page.locator('#login-email').fill(role+'@example.com');await page.locator('#login-password').fill(process.env.PLANNING_TEST_PASSWORD);await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.locator('#plan-list').waitFor();return {context,page,faults};}
 const admin=await open('admin');const page=admin.page;
 // Exercise real catalog writes through the UI before generation.
 await page.locator('#navigation [data-view=courses]').click();
 await page.locator('#entity-form [name=code]').fill('BROWSER');
 await page.locator('#entity-form [name=name]').fill('Browser acceptance course');
 const createdResponse=page.waitForResponse(r=>r.url().endsWith('/api/v1/courses')&&r.request().method()==='POST');
 await page.locator('#entity-form button').click();
 const created=await createdResponse;assert.equal(created.status(),201);const course=await created.json();
 await page.locator(`[data-edit-record="${course.id}"]`).click();
 await page.locator('#catalog-dialog [name=name]').fill('Updated browser course');
 await page.locator('#catalog-dialog [type=submit]').click();await page.locator('#catalog-dialog').waitFor({state:'detached'});
 assert((await page.locator('#content').innerText()).includes('Updated browser course'));
 await page.locator(`[data-delete-record="${course.id}"]`).click();
 await page.locator('#catalog-dialog [data-cancel]').click();await page.locator('#catalog-dialog').waitFor({state:'detached'});
 assert.equal(await page.locator(`[data-edit-record="${course.id}"]`).count(),1);
 await page.locator(`[data-delete-record="${course.id}"]`).click();await page.locator('#catalog-dialog [type=submit]').click();
 await page.locator('#catalog-dialog').waitFor({state:'detached'});assert.equal(await page.locator(`[data-edit-record="${course.id}"]`).count(),0);
 // Real manual create/edit/delete must refresh the new planning calendar.
 const data={};for(const key of ['courses','student-groups','instructors','rooms','time-slots']){
  const response=await page.request.get(base+'/api/v1/'+key,{headers:headers.admin});assert.equal(response.status(),200);data[key]=await response.json();
 }
 await page.locator('#navigation [data-view=timetable]').click();await page.locator('#add-manual-class').click();
 await page.locator('#schedule-editor [data-schedule-day]').selectOption('1');
 const course0=data.courses.find(r=>r.code==='TEST0'),group0=data['student-groups'].find(r=>r.code==='GR-0');
 const teacher0=data.instructors.find(r=>r.email==='teacher@example.com'),room0=data.rooms.find(r=>r.name==='D101');
 const slots=data['time-slots'].filter(r=>r.day_of_week===1).sort((a,b)=>a.start_time.localeCompare(b.start_time));
 for(const [field,value] of Object.entries({course_id:course0.id,group_id:group0.id,instructor_id:teacher0.id,room_id:room0.id,time_slot_id:slots[0].id}))await page.locator(`#schedule-editor [name=${field}]`).selectOption(value);
 await page.locator('#schedule-editor [type=submit]').click();await page.locator('#schedule-editor').waitFor({state:'detached'});
 await page.locator('#plan-list [data-edit-schedule]').click();
 await page.locator('#schedule-editor [name=time_slot_id]').selectOption(slots[1].id);
 await page.locator('#schedule-editor [type=submit]').click();await page.locator('#schedule-editor').waitFor({state:'detached'});
 assert((await page.locator('#plan-list').innerText()).includes('09:00'));
 await page.locator('#plan-list [data-plan-delete]').click();await page.locator('#cancel-schedule-delete').click();
 assert.equal(await page.locator('#plan-list [data-plan-delete]').count(),1);
 await page.locator('#plan-list [data-plan-delete]').click();await page.locator('#confirm-schedule-delete').click();
 await page.locator('#plan-list [data-plan-delete]').waitFor({state:'detached'});
 await page.locator('#navigation [data-view=generate]').click();await page.locator('#plan-run').waitFor();
 page.once('dialog',d=>d.accept());await page.locator('#plan-run').click();await page.locator('#planning-notice').filter({hasText:'Quality score:'}).waitFor();
 await page.locator('#plan-review').click();await page.locator('#plan-list tbody tr').nth(3).waitFor();assert.equal(await page.locator('#plan-list tbody tr').count(),4);
 await page.locator('#plan-publish').click();await page.locator('#planning-publish-dialog [data-confirm]').click();await page.locator('#planning-publish-dialog').waitFor({state:'detached'});await page.locator('#planning-notice').filter({hasText:'published'}).waitFor();
 await page.screenshot({path:'/tmp/planning-live-admin.png',fullPage:true});assert.deepEqual(admin.faults,[]);
 for(const role of ['student','teacher']){const view=await open(role);assert.equal(await view.page.locator('#plan-list tbody tr').count(),2);assert(!(await view.page.locator('#plan-list').innerText()).includes('GR-1'));await view.page.setViewportSize({width:390,height:900});await view.page.waitForTimeout(450);assert(await view.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await view.page.screenshot({path:`/tmp/planning-live-${role}.png`,fullPage:true});assert.deepEqual(view.faults,[]);await view.context.close();}
 await admin.context.close();console.log('PASS real browser → FastAPI → PostgreSQL: password login for three roles, catalog CRUD/cancel, manual create/edit/delete/cancel, generate, review, publish, authorized personal views and mobile layout. Synthetic test accounts only.');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
