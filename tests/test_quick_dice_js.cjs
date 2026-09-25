const assert = require('node:assert/strict');
const {valid, addHistory, breakdown} = require('../app/static/quick_dice.js');
const result = i => ({id:String(i),notation:'2d6-1',question:'Decision',coin:'',faces:[2,3],modifier:-1,total:4});
assert(valid(result(1)));
for (const bad of [null, {}, {...result(1), faces:[0]}, {...result(1), total:NaN}, {...result(1), faces:[1001]}]) assert(!valid(bad));
assert.equal(addHistory(Array.from({length:20},(_,i)=>result(i)),result(25)).length,10);
assert.equal(addHistory([result(1),result(2)],result(1)).length,2);
assert.equal(addHistory([result(1)],result(2)).length,2); // Same outcome is still a separate throw.
assert.equal(addHistory('broken',result(1)).length,1);
assert.equal(breakdown(result(1)),'2d6-1 · 2 + 3 − 1 = 4');
console.log('Quick Dice client tests passed.');
