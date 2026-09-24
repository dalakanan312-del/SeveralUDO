// Isolated preview only: no live save requests, changes or roll submissions.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const base='http://127.0.0.1:9894';
const output=path.resolve(__dirname,'../../navigation-preview');fs.mkdirSync(output,{recursive:true});
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    for(const mode of ['dark','light'])for(const width of [1440,390]){
      const context=await browser.newContext({viewport:{width,height:860},acceptDownloads:true});
      const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
      await page.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
      await page.goto(base+'/p/today?theme='+mode);
      const back=page.locator('.tracker-navigation [data-tracker-back]');
      assert.equal(await back.isVisible(),true);
      await back.click();await page.waitForURL(base+'/p/today');
      await page.goto(base+'/p/today?theme='+mode);
      // Insert sample photo links as galleries do. Downloads keep their normal behavior.
      await page.evaluate(()=>{
        const box=document.createElement('section');box.id='sample-photos';
        box.innerHTML='<a id="sample-photo" href="/portraits/sample/default" target="_blank"><img width="80" alt="Sample portrait" src="/portraits/sample/default"></a><a id="missing-photo" href="/portraits/missing/default">Missing photo</a>';
        document.querySelector('main').append(box);document.dispatchEvent(new Event('htmx:afterSettle'));
      });
      assert.equal(await page.locator('#sample-photo').getAttribute('href'),base+'/photos/sample/default');
      assert.equal(await page.locator('#sample-photo').getAttribute('target'),null);
      await page.locator('#sample-photo').scrollIntoViewIfNeeded();
      await page.locator('#sample-photo').focus();const scroll=await page.evaluate(()=>scrollY);
      await page.locator('#sample-photo').click();
      const dialog=page.locator('dialog.photo-viewer');await dialog.waitFor();
      await page.waitForFunction(()=>document.querySelector('dialog.photo-viewer img')?.naturalWidth===1600);
      assert.equal(context.pages().length,1,'No stray raw-image tab');
      const photo=await dialog.locator('img').boundingBox(),viewport=await dialog.locator('.photo-viewer-image').boundingBox();
      assert.ok(photo.height<=viewport.height+1,'Portrait fits the viewport');
      assert.equal(await dialog.getByRole('button',{name:'✕ Close photo',exact:true}).isVisible(),true);
      await page.screenshot({path:path.join(output,mode+'-'+width+'.png')});
      await dialog.getByRole('button',{name:'Actual size',exact:true}).click();
      assert.equal(await dialog.getByRole('button',{name:'Fit to screen',exact:true}).getAttribute('aria-pressed'),'true');
      const download=page.waitForEvent('download');await dialog.getByRole('link',{name:'Download',exact:true}).click();
      assert.ok((await download).suggestedFilename());
      await page.keyboard.press('Escape');await dialog.waitFor({state:'detached'});
      assert.equal(await page.evaluate(()=>document.activeElement.id),'sample-photo');
      assert.ok(Math.abs((await page.evaluate(()=>scrollY))-scroll)<3,'Photo closes without losing scroll');
      await page.locator('#sample-photo').click();await page.evaluate(()=>window.decadesNavigateBack());
      await dialog.waitFor({state:'detached'});
      await page.locator('#missing-photo').click();await dialog.getByRole('status').filter({hasText:'could not be loaded'}).waitFor();
      await dialog.getByRole('button',{name:'✕ Close photo',exact:true}).click();await dialog.waitFor({state:'detached'});
      assert.equal(await page.evaluate(()=>document.body.classList.contains('photo-viewer-open')),false);
      // Navigation entries survive HTMX's state replacement and return to the page.
      await page.evaluate(()=>{history.pushState({htmx:true},'', '/p/sims');history.replaceState({htmx:true},'',location.href);});
      await page.evaluate(()=>window.decadesNavigateBack());await page.waitForURL(base+'/p/today?theme='+mode);
      if(width>800){
        const sims=page.locator('.app-nav a[href="/p/sims"]').first();
        await sims.evaluate(link=>{for(let parent=link.parentElement;parent;parent=parent.parentElement)if(parent.tagName==='DETAILS')parent.open=true;});
        await sims.click();await page.waitForURL(base+'/p/sims');
        await page.locator('.tracker-navigation [data-tracker-back]').click();await page.waitForURL(base+'/p/today?theme='+mode);
        assert.equal(await page.locator('.tracker-navigation').count(),1,'Back controls survive fragment navigation');
      }
      assert.deepEqual(errors,[]);
      console.log(JSON.stringify({mode,width,photoFit:true,escape:true,back:true,download:true,failedPhotoClose:true}));
      await context.close();
    }
    const context=await browser.newContext({javaScriptEnabled:false,viewport:{width:390,height:860}});
    const page=await context.newPage();await page.goto(base+'/photos/sample/default');
    assert.equal(await page.getByRole('link',{name:'Close photo',exact:true}).isVisible(),true);
    const exit=await page.locator('.tracker-navigation [data-tracker-back]').boundingBox();
    assert.ok(exit&&exit.y>=0&&exit.y+exit.height<860);
    await page.mouse.click(exit.x+exit.width/2,exit.y+exit.height/2);await page.waitForURL(base+'/p/today');
    console.log('Standalone photo viewer has working fallback navigation with JavaScript disabled.');
    await context.close();
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
