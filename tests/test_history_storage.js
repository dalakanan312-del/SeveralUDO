const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../app/static/history-storage.js'),'utf8');
function setup(responses, cancel=false) {
  const events={}, calls=[];
  const start={dataset:{save:'owned-save'},isConnected:true,addEventListener:(k,f)=>events.start=f};
  const stop={hidden:true,addEventListener:(k,f)=>events.stop=f};
  const status={textContent:''};
  const nodes={'compact-all-history':start,'stop-history-compaction':stop,'history-compaction-status':status};
  vm.runInNewContext(source,{document:{getElementById:id=>nodes[id]},URLSearchParams,
    setTimeout:f=>f(),fetch:async(url,options)=>{
      calls.push({url,options});if(cancel)events.stop();
      const result=responses.shift();return {ok:!result.detail,json:async()=>result};
    }});
  return {events,calls,start,stop,status};
}
test('compresses bounded batches and retains the save identity',async()=>{
  const ui=setup([{cursor:250,scanned:250,compressed:200},{cursor:250,scanned:0,compressed:0}]);
  await ui.events.start();assert.equal(ui.calls.length,2);
  assert.equal(ui.calls[1].options.body.get('cursor'),'250');
  assert.equal(ui.calls[1].options.body.get('save_id'),'owned-save');
  assert.match(ui.status.textContent,/All history is retained.*Complete/);assert.equal(ui.start.disabled,false);
});
test('stop finishes the current batch without deleting or continuing',async()=>{
  const ui=setup([{cursor:250,scanned:250,compressed:200}],true);await ui.events.start();
  assert.equal(ui.calls.length,1);assert.match(ui.status.textContent,/Stopped safely/);
});
test('server refusal is visible and stops the loop',async()=>{
  const ui=setup([{detail:'The open save changed.'}]);await ui.events.start();
  assert.equal(ui.calls.length,1);assert.match(ui.status.textContent,/open save changed/);assert.equal(ui.start.disabled,false);
});
test('leaving the page stops further batches',async()=>{
  const ui=setup([]);ui.start.isConnected=false;await ui.events.start();assert.equal(ui.calls.length,0);
});
