const {chromium}=require('./browser.cjs'),fs=require('fs'),assert=require('assert');
(async()=>{const browser=await chromium.launch({headless:true,...(process.env.BROWSER_CHANNEL?{channel:process.env.BROWSER_CHANNEL}:{})});try{
const page=await browser.newPage();const faults=[];page.on('pageerror',e=>faults.push(e.message));let blocked=false,writes=[];
const data={courses:[{id:'c1',code:'CS1',name:'Math',description:'Core'}],rooms:[{id:'r1',room_type:'practical',building:'I',name:'I312',capacity:20,is_active:true}],groups:[{id:'g1',name:'CS101',code:'CS101',academic_year:'2026-2027',student_count:20,is_active:true}],slots:[]};
await page.route('http://app.test/**',route=>{const req=route.request(),path=new URL(req.url()).pathname;const json=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});if(path==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync('index.html','utf8')});const segments=path.split('/'),key=({'student-groups':'groups'})[segments[3]]||segments[3];if(req.method()==='GET')return json(data[key]||[]);writes.push(req.method());if(blocked)return json({detail:'Record is used by a schedule'},409);if(req.method()==='PATCH'){data[key][0]={...data[key][0],...req.postDataJSON()};return json(data[key][0]);}return route.fulfill({status:204});});
await page.goto('http://app.test/');await page.evaluate(data=>{localStorage.setItem(TOKEN_KEY,'test');tokenPayload={role:'admin'};cachedData=data;document.getElementById('login-view').classList.add('hidden');document.getElementById('app-view').classList.remove('hidden');},data);
for(const key of ['courses','rooms','groups']){
 await page.evaluate(key=>{activeView=key;renderEntityView(key);},key);
 await page.locator('[data-edit-record]').click();
 if(key==='rooms'){assert.equal(await page.locator('#catalog-dialog [name=name]').inputValue(),'I312');assert.equal(await page.locator('#catalog-dialog [name=capacity]').inputValue(),'20');await page.locator('#catalog-dialog [name=room_type]').selectOption('lecture');assert.equal(await page.locator('#catalog-dialog [name=capacity]').inputValue(),'70');}
 if(key==='courses')await page.locator('#catalog-dialog [name=name]').fill('Physics');
 if(key==='groups')await page.locator('#catalog-dialog [name=student_count]').fill('25');
 await page.locator('#catalog-dialog [type=submit]').click();await page.locator('#catalog-dialog').waitFor({state:'detached'});
 await page.locator('[data-delete-record]').click();await page.locator('#catalog-dialog [data-cancel]').click();await page.locator('#catalog-dialog').waitFor({state:'detached'});assert.equal(await page.locator('[data-edit-record]').count(),1);
 blocked=true;await page.locator('[data-delete-record]').click();await page.locator('#catalog-dialog [type=submit]').click();await page.locator('[data-catalog-error]').filter({hasText:'used by a schedule'}).waitFor();assert.equal(await page.locator('[data-edit-record]').count(),1);
 blocked=false;await page.locator('#catalog-dialog [type=submit]').click();await page.locator('#catalog-dialog').waitFor({state:'detached'});assert.equal(await page.locator('[data-edit-record]').count(),0);
}
await page.evaluate(()=>{activeView='slots';renderEntityView('slots');});assert.equal(await page.locator('#slot-create-section #entity-form').count(),0);assert.equal(await page.locator('#slot-create-section #bulk-slots').count(),1);assert.equal(await page.locator('form form').count(),0);
assert.deepEqual(faults,[]);assert.equal(writes.filter(x=>x==='PATCH').length,3);console.log('PASS catalog edit/delete, cancellation, dependency errors, room defaults and integrated grid.');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
