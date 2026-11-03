const {chromium}=require('./browser.cjs');
const fs=require('fs');
const assert=require('assert');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.BROWSER_CHANNEL?{channel:process.env.BROWSER_CHANNEL}:{})});
 try {
  const page=await browser.newPage();const faults=[];page.on('pageerror',e=>faults.push(e.message));
  const users=[{id:'u1',email:'instructor@example.com',role:'instructor'},{id:'u2',email:'admin@example.com',role:'admin'},{id:'u3',email:'linked@example.com',role:'instructor'}];
  let instructors=[{id:'i3',user_id:'u3',email:'linked@example.com',department:'Math'}], posted;
  await page.route('http://app.test/**',route=>{
   const req=route.request(),path=new URL(req.url()).pathname;
   const json=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
   if(path==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync('index.html','utf8')});
   if(path==='/api/v1/users')return json(users);
   if(path==='/api/v1/instructors'&&req.method()==='POST'){posted=req.postDataJSON();const row={id:'i1',email:users[0].email,...posted};instructors.push(row);return json(row,201);}
   if(path==='/api/v1/instructors')return json(instructors);
   return json({},404);
  });
  await page.goto('http://app.test/');
  await page.evaluate(({users,instructors})=>{localStorage.setItem(TOKEN_KEY,'test');tokenPayload={role:'admin'};activeView='instructors';cachedData={users,instructors};document.getElementById('login-view').classList.add('hidden');document.getElementById('app-view').classList.remove('hidden');renderEntityView('instructors');},{users,instructors});
  assert.deepEqual(await page.locator('[name=user_id] option').allTextContents(),['Select an instructor by email','instructor@example.com']);
  await page.locator('[name=user_id]').selectOption('u1');await page.locator('[name=department]').fill('SITAM');
  await page.locator('#entity-form button').click();
  await page.getByText('Instructor created.',{exact:true}).first().waitFor();
  assert.equal(posted.user_id,'u1');assert.equal(posted.department,'SITAM');
  await page.waitForFunction(()=>document.querySelector('#entity-form button').disabled);
  assert.equal(await page.locator('[name=user_id] option').count(),1);
  assert.deepEqual(faults,[]);
  console.log('PASS: instructor email picker excludes admin/linked users, submits real ID, and disables empty form.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
