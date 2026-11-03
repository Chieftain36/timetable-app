const {chromium} = require('./browser.cjs');
const fs = require('fs');
const assert = require('assert');
(async () => {
  const browser = await chromium.launch({headless:true, ...(process.env.BROWSER_CHANNEL ? {channel:process.env.BROWSER_CHANNEL} : {})});
  try {
    const page = await browser.newPage({viewport:{width:1120,height:820},locale:'ru-RU'});
    let requests = 0;
    await page.route('http://app.test/**', route => {
      if (new URL(route.request().url()).pathname === '/') {
        return route.fulfill({contentType:'text/html',body:fs.readFileSync('index.html','utf8')});
      }
      requests++;
      return route.fulfill({status:401,contentType:'application/json',body:JSON.stringify({detail:'Invalid credentials'})});
    });
    await page.goto('http://app.test/');
    await page.getByRole('button',{name:'Sign in',exact:true}).click();
    assert.equal(await page.locator('#login-error').textContent(),'Enter your email and password.');
    assert.equal(requests,0);
    await page.locator('#login-email').fill('not-email');
    await page.locator('#login-password').fill('wrong');
    await page.getByRole('button',{name:'Sign in',exact:true}).click();
    assert.equal(await page.locator('#login-error').textContent(),'Enter a valid email address.');
    assert.equal(requests,0);
    await page.locator('#login-email').fill('admin@example.com');
    await page.getByRole('button',{name:'Show password'}).click();
    assert.equal(await page.locator('#login-password').getAttribute('type'),'text');
    await page.getByRole('button',{name:'Hide password'}).click();
    await page.getByRole('button',{name:'Sign in',exact:true}).click();
    await page.getByText('Incorrect email or password. Please try again.').waitFor();
    assert.equal(requests,1);
    if (process.env.SCREENSHOT_PATH) await page.screenshot({path:process.env.SCREENSHOT_PATH});
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
    assert(!/[А-Яа-яЁё]/.test(await page.locator('body').innerText()));
    console.log('PASS: English login errors with Russian browser locale, password toggle and mobile width.');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
