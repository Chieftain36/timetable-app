const {chromium}=require('./browser.cjs');
const fs=require('fs'),assert=require('assert');
const id=n=>'00000000-0000-0000-0000-'+String(n).padStart(12,'0');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
 for(const role of ['admin','student','instructor']){
  const context=await browser.newContext(),page=await context.newPage();page.setDefaultTimeout(10000);
  const faults=[],requests=[],writes=[];page.on('pageerror',e=>faults.push(e.message));
  const monday=new Date();monday.setDate(monday.getDate()-(monday.getDay()+6)%7);const date=d=>d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
  const week=date(monday);
  let prefs={avoid_early:false,earliest_preferred:'09:00:00',minimize_gaps:true,balance_days:true,max_classes_per_day:4,early_weight:2,gap_weight:3,balance_weight:1,daily_weight:2};
  let status={published_version:1,published_at:'2026-11-01T12:00:00Z',group_name:'Group A',draft_count:2,locked_count:0,versions:[]};
  let failure=false;
  const rows=[0,1].map(n=>({id:id(10+n),course_id:id(20+n),group_id:id(30+n),instructor_id:id(40+n),room_id:id(50+n),time_slot_id:id(60+n),course_code:'CS'+n,course_name:'Course '+n,group_name:'Group '+(n?'B':'A'),room_name:n?'A1':'D101',instructor_email:n?'other@example.com':'teacher@example.com',day_of_week:1,start_time:'09:00:00',end_time:'10:00:00',is_locked:false}));
  const courses=rows.map(r=>({id:r.course_id,code:r.course_code,name:r.course_name,is_active:true}));
  const groups=rows.map(r=>({id:r.group_id,name:r.group_name,student_count:20,is_active:true}));
  const instructors=rows.map(r=>({id:r.instructor_id,email:r.instructor_email,is_active:true}));
  const slots=rows.map(r=>({id:r.time_slot_id,day_of_week:1,start_time:r.start_time,end_time:r.end_time,is_active:true}));
  const paths={};for(const p of ['timetable','settings','course-rules','status','student-links','term'])paths['/api/v1/planning/'+p]={get:{},put:{}};paths['/api/v1/planning/generate']={post:{}};paths['/api/v1/instructors/me/availability']={get:{},put:{}};
  await context.route('**/*',async route=>{
   const req=route.request(),url=new URL(req.url()),path=url.pathname;requests.push(path);
   const json=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
   if(path==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync('index.html','utf8')});
   if(path==='/openapi.json')return json({paths});
   if(req.method()!=='GET')writes.push({path,body:req.postDataJSON()});
   if(path==='/api/v1/planning/timetable')return json(url.searchParams.get('week_start')!==week?[]:role==='admin'?rows:[rows[0]]);
   if(path==='/api/v1/planning/status')return json(status);
   if(path==='/api/v1/planning/settings'){if(req.method()==='PUT')prefs=req.postDataJSON();return json(prefs);}
   if(path==='/api/v1/planning/term')return json({start:null,end:null});
   if(path==='/api/v1/planning/course-rules')return json(courses.map(c=>({course_id:c.id,code:c.code,name:c.name,room_type:'practical',required_features:[]})));
   if(path.startsWith('/api/v1/planning/course-rules/'))return json(req.postDataJSON());
   if(path==='/api/v1/planning/student-links')return json([{user_id:id(70),email:'student@example.com',group_id:groups[0].id}]);
   if(path.startsWith('/api/v1/planning/student-links/'))return json(req.postDataJSON());
   if(path.startsWith('/api/v1/planning/locks/')){rows.find(r=>r.id===path.split('/').pop()).is_locked=req.postDataJSON().locked;return json({});}
   if(path==='/api/v1/planning/publish'){status.published_version++;return json({version:status.published_version,published_count:2});}
   if(path==='/api/v1/planning/generate')return failure?json({detail:{message:'Infeasible schedule',unscheduled:[{reason:'No suitable room'}]}},422):json({generated_count:2,quality:{score:85},log:['Validated hard rules','Saved draft'],retained_count:0},201);
   if(path==='/api/v1/instructors/me/availability')return json({slots:[slots[0]],entries:[{time_slot_id:slots[0].id,status:'available'}],max_unavailable_slots:0,min_available_minutes:0});
   const catalog={'courses':courses,'student-groups':groups,'instructors':instructors,'time-slots':slots,'schedules':rows,'rooms':[],'users':[],'teaching-loads':[{id:id(80),is_active:true}]};
   return Object.hasOwn(catalog,path.split('/').pop())?json(catalog[path.split('/').pop()]):json({},404);
  });
  const token='mock.'+Buffer.from(JSON.stringify({sub:id(1),email:role+'@example.com',role,exp:Math.floor(Date.now()/1000)+3600})).toString('base64url')+'.mock';
  await context.addInitScript(token=>localStorage.setItem('university-timetable-token',token),token);
  await page.goto('http://app.test/');await page.locator('#plan-list').waitFor();
  if(role==='admin'){
   assert.equal(await page.locator('#plan-list tbody tr').count(),2);
   await page.locator('[data-plan-filter=group]').selectOption(groups[0].id);
   assert.equal(await page.locator('#plan-list tbody tr').count(),1);
   assert((await page.locator('#plan-list').innerText()).includes('Group A'));
   await page.locator('[data-plan-filter=course]').selectOption(courses[1].id);
   assert((await page.locator('#plan-list').innerText()).includes('No classes match'));
   await page.locator('#plan-clear').click();
   await page.locator('#plan-search').fill('Course 1');assert.equal(await page.locator('#plan-list tbody tr').count(),1);
   await page.locator('#plan-clear').click();
   await page.locator('[data-calendar-action=next]').click();await page.getByText('No classes for this week',{exact:true}).waitFor();
   await page.locator('[data-calendar-action=prev]').click();await page.locator('[data-plan-lock]').first().waitFor();
   await page.locator('[data-plan-lock]').first().click();await page.getByRole('button',{name:'Unlock',exact:true}).waitFor();
   assert(await page.locator('[data-edit-schedule]').first().isDisabled());
   await page.locator('#plan-publish').click();await page.locator('#planning-publish-dialog [data-cancel]').click();assert(!writes.some(w=>w.path.endsWith('/publish')));
   await page.locator('#plan-publish').click();await page.locator('#planning-publish-dialog [data-confirm]').click();await page.locator('#planning-publish-dialog').waitFor({state:'detached'});
   await page.locator('#navigation [data-view=generate]').click();await page.locator('#plan-run').waitFor();
   await page.locator('#plan-preferences [name=max_classes_per_day]').fill('3');await page.locator('#plan-preferences [type=submit]').click();await page.getByText('Generation preferences saved.',{exact:true}).waitFor();assert.equal(prefs.max_classes_per_day,3);
   await page.locator('#plan-defaults').click();await page.getByText('Default preferences restored and saved.',{exact:true}).waitFor();assert.equal(prefs.max_classes_per_day,4);
   const beforeCancelledGeneration=writes.length;page.once('dialog',d=>d.dismiss());await page.locator('#plan-run').click();await page.waitForFunction(()=>!document.getElementById('plan-run').dataset.busy);assert.equal(writes.length,beforeCancelledGeneration,'Cancelled generation must not save settings, rules or schedules');
   failure=true;page.once('dialog',d=>d.accept());await page.locator('#plan-run').click();await page.locator('#planning-notice').filter({hasText:'No suitable room'}).waitFor();assert(await page.locator('#plan-run').isEnabled());
   failure=false;page.once('dialog',d=>d.accept());await page.locator('#plan-run').click();await page.locator('#planning-notice').filter({hasText:'Quality score: 85/100'}).waitFor();
   for(const width of [1440,768,390]){await page.setViewportSize({width,height:1000});for(const theme of ['light','dark']){await page.evaluate(t=>stage2aApplyTheme(t),theme);await page.waitForTimeout(450);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${width} ${theme}`);await page.screenshot({path:`/tmp/planning-generation-${width}-${theme}.png`,fullPage:true});}}
  }else{
   assert.equal(await page.locator('#plan-list tbody tr').count(),1);
   assert(!(await page.locator('#content').innerText()).includes('other@example.com'));
   assert(!requests.some(path=>['/api/v1/users','/api/v1/courses','/api/v1/student-groups','/api/v1/instructors'].includes(path)));
   assert.equal(await page.locator('#plan-publish').count(),0);
   await page.setViewportSize({width:390,height:900});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   if(role==='instructor'){assert.equal(await page.locator('#navigation [data-view=availability]').count(),1);await page.evaluate(()=>stage2aNavigate('availability'));await page.getByRole('heading',{name:'My availability'}).waitFor();}
  }
  assert.deepEqual(faults,[]);await context.close();
 }
 console.log('PASS Sprint 4–5 UI: admin generation/settings/reset/errors/score, publish confirmation, locks, AND filters/search, week navigation, personal isolation, instructor availability and responsive light/dark forms (mock APIs).');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
