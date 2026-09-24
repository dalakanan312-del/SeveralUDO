const test=require('node:test');const assert=require('node:assert/strict');
const {photoURL,internalReferrer}=require('../app/static/navigation.js');
const base='http://127.0.0.1:9876/p/today';
test('only own photo routes open in the viewer, preserving image versions',()=>{
  assert.equal(photoURL('/photos/abc/default?v=17&download=1',base).href,'http://127.0.0.1:9876/portraits/abc/default?v=17');
  for(const value of ['/portraits/id','/portraits/id/default/delete','/api/rolls/1/roll','https://other.example/portraits/id/default','javascript:alert(1)'])assert.equal(photoURL(value,base),null);
});
test('Back does not return to an external login, desktop loading page or raw endpoint',()=>{
  for(const value of ['', 'file:///loading.html','http://127.0.0.1:9876/api/export','https://outside.example/p/today'])assert.equal(internalReferrer(value,base),false);
  for(const path of ['/','/p/today','/sims/abc','/photos/abc/default'])assert.equal(internalReferrer(new URL(path,base).href,base),true);
});
