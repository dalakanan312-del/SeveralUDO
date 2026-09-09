const test=require('node:test');const assert=require('node:assert/strict');
const {cleanFilters,gameLabel,weekdayLabel}=require('../app/static/usability.js');
test('private editing fields never become remembered filters',()=>assert.deepEqual(cleanFilters(new URLSearchParams('q=Cooley&password=secret&birthplace=private&record_status=living')),{q:'Cooley',record_status:'living'}));
test('midnight is a real clock value',()=>assert.equal(gameLabel({game_day:0,hour:0,minute:0}),'Sunday · Day 0 · 00:00'));
test('missing report is not a zero day',()=>assert.equal(gameLabel({game_day:null}),'No report yet'));
test('multi-select filters keep every selection',()=>assert.deepEqual(cleanFilters(new URLSearchParams('kind=birth&kind=death')),{kind:['birth','death']}));
test('game weekday wraps from Saturday to Sunday at midnight',()=>{
  assert.equal(gameLabel({game_day:34,hour:23,minute:59}),'Saturday · Day 34 · 23:59');
  assert.equal(gameLabel({game_day:35,hour:0,minute:0}),'Sunday · Day 35 · 00:00');
});
test('reported weekday is not inferred from tracker global day or year length',()=>{
  assert.equal(weekdayLabel({game_day:29,global_day:101,days_per_year:12}),'Monday · last reported in game');
  assert.equal(weekdayLabel({game_day:0,global_day:101}),'Sunday · last reported in game');
});
test('without a report the tracker weekday is clearly labelled and one-based',()=>{
  assert.equal(weekdayLabel({game_day:null,global_day:1}),'Sunday · tracker weekday');
  assert.equal(weekdayLabel({game_day:null,global_day:100}),'Monday · tracker weekday');
  assert.equal(weekdayLabel({}),'Weekday unavailable');
});
test('live clock events refresh the weekday without reloading Today',()=>{
  const vm=require('node:vm'),fs=require('node:fs');
  const listeners={},headline={textContent:''},fields={};
  const root={dataset:{},querySelector:selector=>fields[selector]??={dataset:{}}};
  const document={
    addEventListener:(name,handler)=>listeners[name]=handler,
    querySelector:selector=>selector==='.u-clock'?root:null,
    querySelectorAll:selector=>selector==='[data-live-weekday]'?[headline]:[],
  };
  vm.runInNewContext(fs.readFileSync(require.resolve('../app/static/usability.js'),'utf8'),{document,URLSearchParams});
  const report={state:'connected',label:'Receiving reports',detail:'',save_name:'Test',global_day:100,last_success:null};
  listeners['decades:clock']({detail:{...report,game_day:34,hour:23,minute:59}});
  assert.equal(headline.textContent,'Saturday · last reported in game');
  listeners['decades:clock']({detail:{...report,game_day:35,hour:0,minute:0}});
  assert.equal(headline.textContent,'Sunday · last reported in game');
  assert.equal(fields['[data-clock-game]'].textContent,'Sunday · Day 35 · 00:00');
  listeners['decades:clock-error']();
  assert.equal(headline.textContent,'Sunday · last reported in game');
  assert.equal(fields['[data-clock-game]'].textContent,'Sunday · Day 35 · 00:00');
});
