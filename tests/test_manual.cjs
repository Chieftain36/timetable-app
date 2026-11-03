const {chromium}=require('./browser.cjs'),fs=require('fs'),assert=require('assert');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.BROWSER_CHANNEL?{channel:process.env.BROWSER_CHANNEL}:{})});
 try{
 const page=await browser.newPage({viewport:{width:1280,height:900}});const faults=[];page.on('pageerror',e=>faults.push(e.message));
 const data={courses:[{id:'c1',code:'CS1',name:'Math',is_active:true}],groups:[{id:'g1',name:'CS101',student_count:20,academic_year:'2026-2027',is_active:true}],instructors:[{id:'i1',email:'teacher@example.com',is_active:true}],rooms:[{id:'r1',name:'D101',building:'D',room_type:'practical',capacity:25,is_active:true}],slots:[1,2].map(id=>({id:'t'+id,day_of_week:1,start_time:id===1?'09:00:00':'10:00:00',end_time:id===1?'10:00:00':'11:00:00',is_active:true})),schedules:[]};
 let blocked=true,writes=[];
 await page.route('http://app.test/**',route=>{
 const req=route.request(),path=new URL(req.url()).pathname;
 const json=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
 if(path==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync('index.html','utf8')});
 if(req.method()==='GET'){const key=path.split('/').pop();return json(data[{'student-groups':'groups','time-slots':'slots'}[key]||key]||[]);}
 if(path.startsWith('/api/v1/schedules')){writes.push(req.postDataJSON());if(blocked)return json({detail:'The instructor is unavailable in this time slot'},409);return json({id:'s1',created_at:'2026-10-12T00:00:00Z',...req.postDataJSON()},req.method()==='POST'?201:200);}
 return json({},404);
 });
 await page.goto('http://app.test/');
 await page.evaluate(data=>{localStorage.setItem(TOKEN_KEY,'test');tokenPayload={role:'admin'};cachedData=data;activeView='rooms';document.getElementById('login-view').classList.add('hidden');document.getElementById('app-view').classList.remove('hidden');renderEntityView('rooms');},data);
 assert.equal(await page.locator('[name=capacity]').getAttribute('min'),'5');assert.equal(await page.locator('[name=capacity]').getAttribute('max'),'25');assert.equal(await page.locator('[name=name] option').count(),36);
 await page.locator('[name=building]').selectOption('I');assert.equal(await page.locator('[name=name] option').last().textContent(),'I312');
 await page.locator('[name=room_type]').selectOption('lecture');assert.equal(await page.locator('[name=building] option').count(),4);assert.equal(await page.locator('[name=name] option').count(),2);assert.equal(await page.locator('[name=capacity]').getAttribute('min'),'1');assert.equal(await page.locator('[name=capacity]').getAttribute('max'),'70');
 await page.locator('[name=building]').selectOption('D');assert.equal(await page.locator('[name=name] option').last().textContent(),'D2');
 await page.evaluate(()=>{activeView='timetable';renderTimetable();});await page.locator('#add-manual-class').click();await page.locator('#schedule-editor [name=course_id]').waitFor();
 await page.locator('#schedule-editor [type=submit]').click();assert.equal(writes.length,0);
 await page.locator('#schedule-editor [data-schedule-day]').selectOption('1');
 for(const [field,value] of Object.entries({course_id:'c1',group_id:'g1',instructor_id:'i1',room_id:'r1',time_slot_id:'t1'}))await page.locator(`#schedule-editor [name=${field}]`).selectOption(value);
 await page.locator('#schedule-editor [type=submit]').click();await page.locator('[data-schedule-error]').filter({hasText:'unavailable'}).waitFor();assert.equal(await page.locator('#schedule-editor [name=course_id]').inputValue(),'c1');
 blocked=false;await page.locator('#schedule-editor [type=submit]').click();await page.locator('#schedule-editor').waitFor({state:'detached'});assert.equal(await page.locator('[data-edit-schedule]').count(),1);
 await page.locator('[data-edit-schedule]').click();await page.locator('#schedule-editor [name=time_slot_id]').waitFor();assert.equal(await page.locator('#schedule-editor [name=time_slot_id]').inputValue(),'t1');await page.locator('#schedule-editor [name=time_slot_id]').selectOption('t2');await page.locator('#schedule-editor [type=submit]').click();await page.locator('#schedule-editor').waitFor({state:'detached'});assert.equal(await page.evaluate(()=>cachedData.schedules[0].time_slot_id),'t2');
 await page.setViewportSize({width:390,height:844});await page.locator('#add-manual-class').click();await page.locator('#schedule-editor [name=course_id]').waitFor();assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.getByRole('button',{name:'Cancel',exact:true}).click();
 assert.deepEqual(faults,[]);console.log('PASS: room selectors/capacity ranges; manual class create/edit, error retention, dynamic timetable and mobile form.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
