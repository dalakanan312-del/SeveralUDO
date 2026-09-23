const test=require('node:test');const assert=require('node:assert/strict');
const {scope,itemKey,matches,unseen,summary}=require('../app/static/death_reminders.js');
const item={id:'sim-1',global_day:100,cause:'Childbirth',source_roll_id:'roll-1'};
const data={user_id:'user',save_id:'save',epoch:'1',branch_id:'main',global_day:100,items:[item],today_count:1,overdue_count:0};
test('deduplication ignores unrelated telemetry and record versions',()=>{
  const seen=[itemKey(item)];
  assert.equal(unseen(data,seen),false);
  assert.equal(unseen({...data,items:[{...item,version:200}]},seen),false);
});
test('new Sims or a changed schedule trigger a new reminder',()=>{
  const seen=[itemKey(item)];
  assert.equal(unseen({...data,items:[item,{...item,id:'sim-2'}]},seen),true);
  assert.equal(unseen({...data,items:[{...item,global_day:99}]},seen),true);
  assert.equal(unseen({...data,items:[{...item,source_roll_id:'new-roll'}]},seen),true);
  assert.equal(unseen({...data,items:[]},seen),false);
});
test('dismissals are scoped to the account, save, branch, checkpoint and day',()=>{
  for(const field of ['user_id','save_id','branch_id','epoch','global_day'])assert.notEqual(scope(data),scope({...data,[field]:'other'}));
});
test('stale save and branch responses cannot show in a new page',()=>{
  const ctx={user:'user',save:'save',epoch:'1'};
  assert.equal(matches(data,ctx),true);
  for(const field of ['user','save','epoch'])assert.equal(matches(data,{...ctx,[field]:'other'}),false);
  assert.equal(matches(data,null),false);
});
test('recovery suppresses reminders, even for newly due Sims',()=>assert.equal(unseen({...data,suppressed:true},[]),false));
test('today and overdue counts remain visibly distinct',()=>{
  assert.equal(summary(data),'1 death due today');
  assert.equal(summary({...data,today_count:2,overdue_count:3}),'2 deaths due today · 3 overdue');
});
