const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,'../app/static/workflow.js'),'utf8');
function setup(hash='#new-family-plan') {
  const events={}, location={hash};
  const outer={tagName:'DETAILS',open:false,parentElement:null};
  const target={tagName:'DETAILS',open:false,parentElement:outer,scrolls:0,scrollIntoView(){this.scrolls++;}};
  let current=target;
  const context={location,window:{addEventListener:(name,fn)=>events[name]=fn},
    document:{addEventListener:(name,fn)=>events[name]=fn,getElementById:id=>id==='new-family-plan'?current:null},
    requestAnimationFrame:fn=>fn()};
  vm.runInNewContext(source,context);
  return {events,location,target,outer,replace:value=>current=value};
}
test('opening a section reveals all collapsed ancestors',()=>{
  const state=setup();assert.equal(state.target.open,true);assert.equal(state.outer.open,true);assert.equal(state.target.scrolls,1);
});
test('fast navigation reveals the newly swapped target',()=>{
  const state=setup();const replacement={tagName:'H2',parentElement:state.outer,scrolls:0,scrollIntoView(){this.scrolls++;}};
  state.outer.open=false;state.replace(replacement);state.events['htmx:afterSettle']();
  assert.equal(state.outer.open,true);assert.equal(replacement.scrolls,1);
});
test('clicking the same anchor again reopens a collapsed form',()=>{
  const state=setup();state.target.open=false;
  state.events.click({target:{closest:()=>({getAttribute:()=>state.location.hash})}});
  assert.equal(state.target.open,true);
});
test('missing, empty and malformed anchors are harmless',()=>{
  for(const hash of ['', '#unknown', '#%not-valid']){const state=setup(hash);assert.equal(state.target.scrolls,0);}
});
