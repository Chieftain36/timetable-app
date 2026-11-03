const {chromium}=require('./browser.cjs'),fs=require('fs'),assert=require('assert');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.BROWSER_CHANNEL?{channel:process.env.BROWSER_CHANNEL}:{})});
 try{
 const page=await browser.newPage();let blocked=false,writes=[];const faults=[];page.on('pageerror',e=>faults.push(e.message));
 let users=[{id:'admin-id',email:'admin@example.com',role:'admin'}, {id:'u1',email:'teacher@example.com',role:'instructor',created_at:'2026-10-12T00:00:00Z'}];
 await page.route('http://app.test/**',async route=>{
 const req=route.request();if(new URL(req.url()).pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync('index.html','utf8')});
 const body=req.method()==='PATCH'?req.postDataJSON():null;writes.push({method:req.method(),body});
 if(blocked)return route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({detail:'This user is referenced by existing records'})});
 if(req.method()==='DELETE')return route.fulfill({status:204});
 users[1]={...users[1],...body};return route.fulfill({contentType:'application/json',body:JSON.stringify(users[1])});
 });
 await page.goto('http://app.test/');
 await page.evaluate(users=>{localStorage.setItem(TOKEN_KEY,'test');tokenPayload={sub:'admin-id',role:'admin'};activeView='users';cachedData={users,instructors:[]};document.getElementById('login-view').classList.add('hidden');document.getElementById('app-view').classList.remove('hidden');renderEntityView('users');},users);
 assert.equal(await page.locator('[data-delete-user="admin-id"]').count(),0);
 await page.evaluate(()=>openUserDialog('admin-id',true));assert.equal(await page.locator('#user-dialog').count(),0);
 await page.locator('[data-edit-user="u1"]').click();assert.equal(await page.locator('#user-dialog [name=email]').inputValue(),'teacher@example.com');assert.equal(await page.locator('#user-dialog [name=password]').inputValue(),'');
 await page.locator('#user-dialog [name=email]').fill('updated@example.com');await page.getByRole('button',{name:'Save changes',exact:true}).click();await page.locator('#user-dialog').waitFor({state:'detached'});
 assert.deepEqual(writes[0],{method:'PATCH',body:{email:'updated@example.com'}});
 await page.locator('[data-edit-user="u1"]').click();await page.locator('#user-dialog [name=password]').fill('short');await page.getByRole('button',{name:'Save changes',exact:true}).click();await page.locator('[data-user-error]').filter({hasText:'8 to 72'}).waitFor();assert.equal(writes.length,1);
 await page.locator('#user-dialog [name=password]').fill('NewPassword123');await page.locator('#user-dialog [name=role]').selectOption('student');await page.getByRole('button',{name:'Save changes',exact:true}).click();await page.locator('#user-dialog').waitFor({state:'detached'});assert.deepEqual(writes[1].body,{password:'NewPassword123',role:'student'});
 await page.locator('[data-delete-user]').click();await page.getByRole('button',{name:'Cancel',exact:true}).click();assert.equal(writes.length,2);
 blocked=true;await page.locator('[data-delete-user]').click();await page.getByRole('button',{name:'Delete user',exact:true}).click();await page.locator('[data-user-error]').filter({hasText:'referenced'}).waitFor();assert.equal(await page.locator('[data-delete-user]').count(),1);
 blocked=false;await page.getByRole('button',{name:'Delete user',exact:true}).click();await page.locator('#user-dialog').waitFor({state:'detached'});assert.equal(await page.locator('[data-delete-user]').count(),0);assert.deepEqual(faults,[]);
 console.log('PASS: user email/role/password editing, unchanged password omission, validation, delete cancel/409/retry and dynamic refresh.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
