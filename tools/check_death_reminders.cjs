// Browser regression against tools/preview_workspace.py and synthetic status only.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
const fs=require('node:fs');
const output=path.resolve(__dirname,'../../death-reminder-preview');
fs.mkdirSync(output,{recursive:true});
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    for(const mode of ['dark','light'])for(const width of [1440,390]){
      const context=await browser.newContext({viewport:{width,height:960}});
      const page=await context.newPage();const errors=[];const posts=[];
      page.on('pageerror',error=>errors.push(error.message));
      page.on('request',request=>{if(request.method()==='POST')posts.push(request.url());});
      let data;
      await page.route('**/*',async route=>{
        const url=new URL(route.request().url());
        if(url.hostname!=='127.0.0.1')return route.abort();
        if(url.pathname!=='/api/live-status')return route.continue();
        const response=await route.fetch();data=await response.json();
        data.death_reminders={...data.death_reminders,today_count:1,overdue_count:1,items:[
          {id:'sample-a',name:'Ada Cooley',cause:'Childbirth complications',global_day:100,profile_url:'/sims/sample-a',review_url:'/p/today?view=tools&task=deaths&due=due#death-sample-a'},
          {id:'sample-b',name:'Ben Cooley',cause:'Famine',global_day:99,profile_url:'/sims/sample-b',review_url:'/p/today?view=tools&task=deaths&due=due#death-sample-b'},
        ]};
        await route.fulfill({response,json:data});
      });
      await page.goto('http://127.0.0.1:9893/p/today?theme='+mode);
      const dialog=page.locator('.death-reminder-dialog');
      await dialog.waitFor({state:'visible'});
      assert.match(await dialog.innerText(),/Ada Cooley/);
      assert.match(await dialog.innerText(),/Overdue · GD 99/);
      assert.match(await dialog.innerText(),/Nothing is marked dead/);
      assert.equal(await page.locator('[data-death-reminders]').isVisible(),true);
      assert.ok(await dialog.evaluate(node=>node.scrollWidth<=node.clientWidth+1),'Dialog fits viewport');
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Page fits viewport');
      assert.equal(await page.evaluate(()=>document.activeElement.textContent),'Remind me later');
      await page.screenshot({path:path.join(output,mode+'-'+width+'.png'),animations:'disabled'});
      await dialog.getByRole('button',{name:'Remind me later'}).click();
      assert.equal(await dialog.count(),0);
      // Identical status does not re-open; dismissal survives navigation/reload.
      await page.reload();await page.waitForResponse('**/api/live-status');
      await page.waitForFunction(()=>!document.querySelector('[data-death-reminders]').hidden);
      assert.equal(await dialog.count(),0);
      await page.getByRole('button',{name:'Review deaths',exact:true}).click();
      assert.equal(await dialog.isVisible(),true);
      await page.keyboard.press('Escape');await dialog.waitFor({state:'detached'});
      // Stop network updates so the following synthetic transitions are deterministic.
      await page.unroute('**/*');
      await page.route('**/api/live-status',route=>route.fulfill({status:503,body:'Test temporarily offline'}));
      const emit=async value=>page.evaluate(detail=>document.dispatchEvent(new CustomEvent('decades:live-status',{detail})),value);
      const next={...data,death_reminders:{...data.death_reminders,today_count:2,items:[...data.death_reminders.items,{...data.death_reminders.items[0],id:'sample-c',name:'Cara Cooley'}]}};
      // A roll preview retains focus; the new reminder waits until it closes.
      await page.evaluate(()=>{const d=document.createElement('dialog');d.id='test-roll';d.textContent='Review before confirming';document.body.append(d);d.showModal();});
      await emit(next);assert.equal(await dialog.count(),0);
      await page.evaluate(()=>document.querySelector('#test-roll').close());
      await dialog.waitFor({state:'visible'});
      assert.match(await dialog.innerText(),/Cara Cooley/);
      // Removed/confirmed deaths close the popup and remove the banner.
      await emit({...data,death_reminders:{...data.death_reminders,items:[],today_count:0,overdue_count:0}});
      await dialog.waitFor({state:'detached'});
      assert.equal(await page.locator('[data-death-reminders]').isVisible(),false);
      // A stale response for another save or branch cannot show a popup.
      await emit({...next,death_reminders:{...next.death_reminders,save_id:'wrong-save'}});
      assert.equal(await dialog.count(),0);
      await emit({...next,death_reminders:{...next.death_reminders,epoch:'wrong-branch'}});
      assert.equal(await dialog.count(),0);
      // The next tracker day is a fresh reminder, even if the Sims are unchanged.
      await emit({...data,death_reminders:{...data.death_reminders,global_day:101,today_count:0,overdue_count:2}});
      await dialog.waitFor({state:'visible'});
      assert.match(await dialog.innerText(),/Overdue deaths to review/);
      assert.deepEqual(posts.filter(url=>!url.endsWith('/api/ui/preferences')),[],'Reminder must not mutate game data');
      assert.deepEqual(errors,[]);
      console.log(JSON.stringify({mode,width,popup:'passed',dismissal:'passed',scope:'passed',nonDestructive:true}));
      await context.close();
    }
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
