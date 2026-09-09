const test=require('node:test');const assert=require('node:assert/strict');
const {cleanFilters,gameLabel}=require('../app/static/usability.js');
test('private editing fields never become remembered filters',()=>assert.deepEqual(cleanFilters(new URLSearchParams('q=Cooley&password=secret&birthplace=private&record_status=living')),{q:'Cooley',record_status:'living'}));
test('midnight is a real clock value',()=>assert.equal(gameLabel({game_day:0,hour:0,minute:0}),'Day 0 · 00:00'));
test('missing report is not a zero day',()=>assert.equal(gameLabel({game_day:null}),'No report yet'));
test('multi-select filters keep every selection',()=>assert.deepEqual(cleanFilters(new URLSearchParams('kind=birth&kind=death')),{kind:['birth','death']}));
