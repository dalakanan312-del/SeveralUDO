const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const code=fs.readFileSync(require.resolve('../app/static/app.js'),'utf8').split('\ninitializeAdvertising();')[0];
function harness(){
  const listeners={},session=new Map(),preferences=new Map(),loaded=[];
  const dialog={hidden:true},config={textContent:JSON.stringify({consent_available:true,available:true,client:'test'})};
  const document={querySelector:s=>s==='#decades-ad-config'?config:s==='#ad-consent-dialog'?dialog:s==='#ad-consent-dialog:not([hidden])'&&!dialog.hidden?dialog:null,
    querySelectorAll:()=>[],addEventListener:(name,fn)=>listeners[name]=fn,
    createElement:()=>({dataset:{},addEventListener(){}}),head:{appendChild:script=>loaded.push(script)}};
  const context={document,sessionStorage:{getItem:key=>session.get(key),setItem:(key,value)=>session.set(key,value)},
    localStorage:{getItem:key=>preferences.get(key),setItem:(key,value)=>preferences.set(key,value)}};
  vm.runInNewContext(code,context);context.initializeAdvertising();
  return{context,dialog,listeners,session,preferences,loaded};
}
test('Not now hides the ad prompt without granting or declining consent',()=>{
  const h=harness();assert.equal(h.dialog.hidden,false);
  h.listeners.click({target:{closest:()=>true}});
  assert.equal(h.dialog.hidden,true);assert.equal(h.preferences.size,0);assert.equal(h.loaded.length,0);
  h.context.initializeAdvertising();assert.equal(h.dialog.hidden,true,'Does not reopen after a fragment update');
  h.context.showAdvertisingDialog();assert.equal(h.dialog.hidden,false,'User can reopen preferences explicitly');
});
test('Escape also defers the prompt without requesting ads',()=>{
  const h=harness();let prevented=false;h.listeners.keydown({key:'Escape',preventDefault(){prevented=true;}});
  assert.equal(prevented,true);assert.equal(h.dialog.hidden,true);assert.equal(h.loaded.length,0);assert.equal(h.preferences.size,0);
});
