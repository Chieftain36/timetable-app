const {chromium} = require('./browser.cjs');
const fs = require('fs');
const assert = require('assert');
(async () => {
  const browser = await chromium.launch({headless:true, ...(process.env.BROWSER_CHANNEL ? {channel:process.env.BROWSER_CHANNEL} : {})});
  try {
    const page = await browser.newPage();
    const faults = [];
    page.on('pageerror', error => faults.push(error.message));
    const patches = [], slots = [];
    let saveFails = false, saved, active = true;
    await page.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      const json = (data,status=200) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
      if (url.pathname === '/') return route.fulfill({contentType:'text/html', body:fs.readFileSync('index.html','utf8').replace(/<script src=[^>]+><\/script>/,'')});
      if (url.pathname === '/api/v1/instructors/t1' && request.method() === 'PATCH') {
        const body = request.postDataJSON(); patches.push(body);
        if (body.is_active === true || body.confirm_deactivation) {active=body.is_active;return json({id:'t1',is_active:active});}
        return json({detail:{code:'confirmation_required'}},409);
      }
      if (url.pathname === '/api/v1/instructors') return json([{id:'t1',email:'teacher@example.com',is_active:active}]);
      if (url.pathname === '/api/v1/time-slots') {
        if (request.method() === 'POST') { const slot={id:String(slots.length+1),...request.postDataJSON()}; slots.push(slot); return json(slot,201); }
        return json(slots);
      }
      if (url.pathname === '/api/v1/instructors/me/availability') {
        if (saveFails) return json({detail:'Saving is not allowed'},409);
        saved = request.postDataJSON(); return json(saved);
      }
      return json({detail:'Not found'},404);
    });
    await page.goto('http://app.test/');
    await page.evaluate(() => {
      localStorage.setItem(TOKEN_KEY,'test'); tokenPayload={role:'admin'}; activeView='instructors';
      document.getElementById('login-view').classList.add('hidden'); document.getElementById('app-view').classList.remove('hidden');
      sprint2Schema={paths:{'/api/v1/instructors/{instructor_id}':{patch:{requestBody:{content:{'application/json':{schema:{properties:{is_active:{},confirm_deactivation:{}}}}}}}}}};
      cachedData.instructors=[{id:'t1',email:'teacher@example.com',is_active:true}];
      renderEntityView('instructors');
    });
    await page.getByRole('button',{name:'Deactivate',exact:true}).click();
    await page.getByText('This instructor has active assignments. Deactivate anyway?').waitFor();
    await page.getByRole('button',{name:'Cancel',exact:true}).click();
    assert.equal(patches.length,1);
    await page.getByRole('button',{name:'Deactivate',exact:true}).click();
    await page.getByRole('button',{name:'Yes',exact:true}).click();
    await page.getByRole('button',{name:'Activate',exact:true}).waitFor();
    assert.deepEqual(patches.map(row=>row.confirm_deactivation),[false,false,true]);
    await page.getByRole('button',{name:'Activate',exact:true}).click();
    await page.getByRole('button',{name:'Deactivate',exact:true}).waitFor();
    assert.deepEqual(patches[3],{is_active:true});
    const guards = await page.evaluate(() => {
      const reject = callback => {try {callback();return false;} catch {return true;}};
      return [
        reject(()=>buildSlots([7],'09:00',90,10,2)),
        reject(()=>buildSlots([1],'23:00',90,0,1)),
        reject(()=>buildSlots([1],'09:00',90,0,1,[{id:'a',day_of_week:1,start_time:'09:30',end_time:'11:00'}])),
        reject(()=>validateSlot({day_of_week:1,start_time:'11:00',end_time:'10:00'},[])),
        reject(()=>validateGroup({code:'CS-1',academic_year:'2026-2027',student_count:20},[{code:'cs-1',academic_year:'2026-2027'}],true)),
        !reject(()=>validateGroup({code:'CS-1',academic_year:'2027-2028',student_count:20},[{code:'cs-1',academic_year:'2026-2027'}],true)),
        reject(()=>requireActiveAssignment({is_active:false},{is_active:true},{is_active:true})),
        reject(()=>requireActiveAssignment({is_active:true},{is_active:false},{is_active:true})),
        reject(()=>requireActiveAssignment({is_active:true},{is_active:true},{is_active:false})),
        reject(()=>nextAvailability('available',1,1)),
        buildSlots([1],'09:00',60,0,2).length === 2,
        ...[4,51,5.5].map(student_count=>reject(()=>validateGroup({code:'CS-1',academic_year:'2026-2027',student_count},[],true))),
        ...[5,50].map(student_count=>!reject(()=>validateGroup({code:'CS-1',academic_year:'2026-2027',student_count},[],true))),
      ];
    });
    assert(guards.every(Boolean));
    const bounds = await page.evaluate(() => {
      document.getElementById('content').innerHTML = fieldMarkup({name:'student_count',label:'Student count',type:'number',min:5,max:50,required:true});
      const input = document.querySelector('[name=student_count]');
      return [4,5,50,51,5.5].map(value => {input.value=String(value);return validateInput(input, /^[0-9]+$/);});
    });
    assert.deepEqual(bounds,[false,true,true,false,false]);
    assert.equal(await page.locator('html').getAttribute('lang'),'en');
    await page.evaluate(()=>{activeView='slots';cachedData.slots=[];renderEntityView('slots');});
    await page.locator('#bulk-slots [name=start]').fill('09:00');
    await page.locator('#bulk-slots [name=end]').fill('11:00');
    await page.locator('#bulk-slots [name=duration]').fill('50');
    await page.locator('#bulk-slots [name=pause]').fill('10');
    await page.locator('#bulk-slots button').click();
    await page.getByText('Slots created: 10. Existing matching slots: 0.',{exact:true}).waitFor();
    assert.equal(slots.length,10);
    await page.evaluate(()=>{
      activeView='availability'; tokenPayload={role:'instructor'};
      window.matrix = renderAvailabilityMatrix(document.getElementById('content'),{
        slots:[1,2,3].map((id,i)=>({id:String(id),day_of_week:1,start_time:`${String(9+i).padStart(2,'0')}:00`,end_time:`${10+i}:00`})),
        entries:[],max_unavailable_slots:2,min_available_minutes:120,
      },payload=>api(SPRINT2_API.availability,{method:'PUT',body:JSON.stringify(payload)}));
    });
    await page.locator('[data-slot-id="1"]').click();
    assert.equal(await page.locator('[data-slot-id="1"]').textContent(),'Unavailable');
    await page.locator('[data-slot-id="2"]').click();
    assert.equal(await page.locator('[data-slot-id="2"]').textContent(),'Available');
    await page.locator('[data-slot-id="1"]').click();
    assert.equal(await page.locator('[data-slot-id="1"]').textContent(),'Preferred');
    saveFails=true;
    await page.locator('[data-save]').click();
    await page.getByText('Saving is not allowed',{exact:true}).waitFor();
    assert(await page.evaluate(()=>window.matrix.hasChanges()));
    saveFails=false;
    await page.locator('[data-save]').click();
    await page.getByText('Availability saved.',{exact:true}).waitFor();
    assert(!await page.evaluate(()=>window.matrix.hasChanges()));
    assert.equal(saved.entries[0].status,'preferred');
    assert.deepEqual(faults,[]);
    console.log('PASS: Chromium UI: confirmation/cancel/retry, bulk creation, overlap/Sunday/year/activity guards, matrix cycle/minimum hours, failed save retention, successful save.');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
