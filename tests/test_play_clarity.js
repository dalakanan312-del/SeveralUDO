const test=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs');const vm=require('node:vm');
const code=fs.readFileSync(require.resolve('../app/static/play_clarity.js'),'utf8');

function harness(responses){
  const nodes=[],listeners={},calls=[],events=[];
  class Element {
    constructor(tag){this.tag=tag;this.children=[];this.listeners={};this.dataset={};this.textContent='';this.isConnected=true;nodes.push(this);}
    append(...children){this.children.push(...children);}
    setAttribute(key,value){this[key]=value;}
    addEventListener(name,fn){this.listeners[name]=fn;}
    querySelectorAll(){return this.children.filter(n=>n.tag==='button');}
    remove(){this.isConnected=false;}
    focus(){}
    showModal(){this.open=true;}
    close(){this.open=false;this.listeners.close?.();}
    async click(){if(!this.disabled&&!this.hidden)await this.listeners.click?.();}
  }
  class Form extends Element {constructor(){super('form');this.action='http://tracker/api/rolls/roll-id/roll';this.dataset.version='1';}}
  const document={body:new Element('body'),activeElement:null,
    createElement:tag=>new Element(tag),querySelector:selector=>selector==='#ui-context'?{dataset:{user:'u',save:'s',epoch:'e'}}:null,
    addEventListener(){},dispatchEvent:e=>events.push(e)};
  const context={document,window:{addEventListener:(name,fn)=>listeners[name]=fn,scrollY:100},
    HTMLFormElement:Form,URL,FormData:class {},location:{href:'http://tracker/p/today',assign(){}},
    CustomEvent:class {constructor(name,options){this.type=name;this.detail=options.detail;}},
    fetch:async(url,options)=>{calls.push({url,options});const next=responses.shift();return{ok:next.status===200,status:next.status,json:async()=>next.data};}};
  vm.runInNewContext(code,context);
  return {nodes,calls,events,dialog:()=>nodes.filter(n=>n.tag==='dialog'&&n.open).at(-1),
    submit:()=>listeners.submit({target:new Form(),preventDefault(){},stopImmediatePropagation(){}})};
}
const preview=token=>({token,label:'Witch trial occurrence',actual:1,outcome:'No trial',counts:{},effects:['Records the result'],kind:'roll'});

test('conflict replaces confirm with refresh, retains original result and waits for a second confirmation',async()=>{
  const h=harness([{status:200,data:{preview:preview('old')}},{status:409,data:{detail:'Consequences changed'}},
    {status:200,data:{preview:preview('fresh')}},{status:200,data:{ok:true,kind:'roll',id:'roll-id'}}]);
  await h.submit();const first=h.dialog();const confirm=first.children.find(n=>n.textContent==='Confirm these changes');
  await confirm.click();assert.equal(confirm.hidden,true);assert.equal(first.children.filter(n=>n.role==='alert').length,1);
  assert.equal(h.calls.length,2);const refresh=first.children.find(n=>n.textContent==='Review updated preview');
  await refresh.click();assert.equal(h.calls[2].url,'/api/previews/old/refresh');assert.equal(h.events.length,0);
  const second=h.dialog();assert.notEqual(second,first);assert.ok(second.children.some(n=>n.textContent==='Result 1 · No trial'));
  await second.children.find(n=>n.textContent==='Confirm these changes').click();
  assert.equal(h.calls[3].url,'/api/previews/fresh/confirm');assert.equal(h.events.length,1);
});

test('repeated network errors update one alert instead of stacking messages',async()=>{
  const h=harness([{status:200,data:{preview:preview('old')}},{status:503,data:{detail:'First failure'}},{status:503,data:{detail:'Second failure'}}]);
  await h.submit();const dialog=h.dialog();const confirm=dialog.children.find(n=>n.textContent==='Confirm these changes');
  await confirm.click();await confirm.click();
  const alerts=dialog.children.filter(n=>n.role==='alert');assert.equal(alerts.length,1);assert.equal(alerts[0].textContent,'Nothing confirmed. Second failure');
});

test('ordinary confirmation is one click and does not refresh or rethrow',async()=>{
  const h=harness([{status:200,data:{preview:preview('current')}},{status:200,data:{ok:true,kind:'roll',id:'roll-id'}}]);
  await h.submit();await h.dialog().children.find(n=>n.textContent==='Confirm these changes').click();
  assert.equal(h.calls.length,2);assert.equal(h.events[0].type,'decades:roll-confirmed');assert.equal(h.dialog(),undefined);
});
