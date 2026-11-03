const {chromium} = require('./browser.cjs');
const fs = require('fs');
const assert = require('assert');
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.BROWSER_CHANNEL?{channel:process.env.BROWSER_CHANNEL}:{})});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1100}});
    const faults=[];page.on('pageerror',e=>faults.push(e.message));
    let slots=[
      {id:'s1',day_of_week:1,start_time:'08:30:00',end_time:'09:20:00',is_active:true},
      {id:'s2',day_of_week:1,start_time:'09:30:00',end_time:'10:20:00',is_active:true},
      {id:'s3',day_of_week:2,start_time:'08:30:00',end_time:'09:20:00',is_active:true},
      {id:'s4',day_of_week:3,start_time:'10:30:00',end_time:'11:20:00',is_active:false},
    ];
    let blocked=false, writes=[];
    await page.route('http://app.test/**',async route=>{
      const request=route.request(),url=new URL(request.url());
      const json=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
      if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync('index.html','utf8')});
      if(request.method()==='GET'&&url.pathname==='/api/v1/time-slots')return json(slots);
      if(['PATCH','DELETE'].includes(request.method())){
        writes.push(request.method());
        await new Promise(resolve=>setTimeout(resolve,120));
        if(blocked)return json({detail:'This slot is scheduled; remove or update its classes first'},409);
        const id=url.pathname.split('/').pop();
        if(request.method()==='DELETE'){slots=slots.filter(row=>row.id!==id);return route.fulfill({status:204});}
        slots=slots.map(row=>row.id===id?{...row,...request.postDataJSON()}:row);
        return json(slots.find(row=>row.id===id));
      }
      return json({detail:'Not found'},404);
    });
    await page.goto('http://app.test/');
    await page.evaluate(rows=>{
      localStorage.setItem(TOKEN_KEY,'test');tokenPayload={role:'admin'};activeView='slots';cachedData.slots=rows;
      document.getElementById('login-view').classList.add('hidden');document.getElementById('app-view').classList.remove('hidden');
      stage2aIdentity();renderNavigation();renderEntityView('slots');
    },slots);
    if(process.env.SCREENSHOT_PATH)await page.screenshot({path:process.env.SCREENSHOT_PATH,fullPage:true});
    await page.locator('[data-slot-day="2"]').click();assert.equal(await page.locator('[data-edit-slot]').count(),1);
    await page.locator('[data-slot-day="0"]').click();
    await page.locator('[data-edit-slot="s1"]').click();
    assert.equal(await page.locator('#slot-dialog [name=start_time]').inputValue(),'08:30');
    await page.locator('#slot-dialog [name=end_time]').fill('10:00');
    await page.getByRole('button',{name:'Save changes',exact:true}).click();
    await page.locator('[data-slot-error]').filter({hasText:'overlap'}).waitFor();assert.equal(writes.length,0);
    await page.locator('#slot-dialog [name=start_time]').fill('08:00');
    await page.locator('#slot-dialog [name=end_time]').fill('08:50');
    await page.getByRole('button',{name:'Save changes',exact:true}).click();
    await page.locator('#slot-dialog').waitFor({state:'detached'});
    assert.equal(slots[0].start_time,'08:00');assert.deepEqual(writes,['PATCH']);
    assert((await page.locator('.slot-table').textContent()).includes('08:00'));
    await page.locator('[data-delete-slot="s1"]').click();
    await page.getByRole('button',{name:'Cancel',exact:true}).click();assert.equal(writes.length,1);
    blocked=true;
    await page.locator('[data-delete-slot="s1"]').click();
    await page.getByRole('button',{name:'Delete slot',exact:true}).click();
    await page.locator('[data-slot-error]').filter({hasText:'scheduled'}).waitFor();assert.equal(slots.length,4);
    blocked=false;
    await page.getByRole('button',{name:'Delete slot',exact:true}).click();
    await page.locator('#slot-dialog').waitFor({state:'detached'});assert.equal(await page.locator('[data-delete-slot="s1"]').count(),0);
    assert.deepEqual(writes,['PATCH','DELETE','DELETE']);
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.locator('[data-edit-slot="s2"]').click();
    assert(await page.locator('#slot-dialog').isVisible());
    await page.keyboard.press('Escape');await page.locator('#slot-dialog').waitFor({state:'detached'});
    assert.deepEqual(faults,[]);
    console.log('PASS: slot edit/prefill/overlap, deletion cancel/409/retry/204, dynamic rows, filters and mobile dialog.');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
