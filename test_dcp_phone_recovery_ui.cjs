'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync(__dirname+'/dcp_phone_recovery.js','utf8');
function response(status=200,extra={}){
 return {ok:status===200,status,json:async()=>({ok:true}),headers:{get:()=> '800'},blob:async()=>({}),...extra};
}
function setup(){
 const elements={};
 for(const id of ['status','app','frame','typing','dryrunStatus','refresh','state','close','viewport','type','up','down','dryrun']){
  elements[id]={textContent:'',value:'',style:{display:'block'},src:'blob:old',
   getAttribute(k){return this[k]||null},removeAttribute(k){delete this[k]}};
 }
 const revoked=[],requests=[];
 class FakeURL extends URL{}
 FakeURL.createObjectURL=()=> 'blob:new';FakeURL.revokeObjectURL=x=>revoked.push(x);
 const context=vm.createContext({document:{getElementById:id=>elements[id]},window:{},
  location:{href:'https://example.test/recovery',pathname:'/recovery'},history:{replaceState(){}},
  URL:FakeURL,setTimeout,fetch:async(path,opts)=>{requests.push({path,opts});return response()}});
 // Startup without Telegram data fails closed without making any network request.
 vm.runInContext(source,context);
 vm.runInContext('bearer="test-only-bearer";viewport={width:1280,height:800}',context);
 return {context,elements,revoked,requests,run:code=>vm.runInContext(code,context)};
}
test('401 clears screenshot, text, viewport and blocks later requests',async()=>{
 const h=setup();h.context.fetch=async()=>response(401);h.elements.typing.value='private input';
 await assert.rejects(h.run('snapshot()'),/Сессия завершена/);
 assert.equal(h.elements.app.style.display,'none');assert.equal(h.elements.frame.getAttribute('src'),null);
 assert.equal(h.elements.typing.value,'');assert.deepEqual(h.revoked,['blob:old']);
 assert.equal(h.run('viewport.width'),0);assert.equal(h.run('bearer'),null);
 let calls=0;h.context.fetch=async()=>{calls++;return response()};
 await assert.rejects(h.run('snapshot()'),/Сессия завершена/);assert.equal(calls,0);
});
test('close hides immediately and sends server revocation with current bearer',async()=>{
 const h=setup();let finish;h.context.fetch=(path,opts)=>{h.requests.push({path,opts});return new Promise(r=>finish=r)};
 const pending=h.elements.close.onclick();
 assert.equal(h.elements.app.style.display,'none');assert.equal(h.elements.frame.getAttribute('src'),null);
 assert.equal(h.requests[0].path,'/recovery/close');assert.equal(h.requests[0].opts.method,'POST');
 assert.equal(h.requests[0].opts.headers.Authorization,'Bearer test-only-bearer');
 finish(response());await pending;assert.match(h.elements.status.textContent,/Доступ закрыт/);
});
test('expired close clears locally and shows ended session',async()=>{
 const h=setup();h.context.fetch=async()=>response(401);await h.elements.close.onclick();
 assert.equal(h.elements.frame.getAttribute('src'),null);assert.match(h.elements.status.textContent,/Сессия завершена/);
});
test('network failure never claims server revocation',async()=>{
 const h=setup();h.context.fetch=async()=>{throw Error('offline')};await h.elements.close.onclick();
 assert.equal(h.elements.frame.getAttribute('src'),null);
 assert.match(h.elements.status.textContent,/Подтверждение от сервера не получено/);
});
test('late screenshot cannot repaint after close',async()=>{
 const h=setup();let finishBlob;h.context.fetch=async()=>response(200,{blob:()=>new Promise(r=>finishBlob=r)});
 const pending=h.run('snapshot()');await new Promise(r=>setImmediate(r));
 h.run('clearAccess("closed")');finishBlob({});await pending;
 assert.equal(h.elements.frame.getAttribute('src'),null);
});
test('valid frame still renders and releases previous blob',async()=>{
 const h=setup();await h.run('snapshot()');assert.equal(h.elements.frame.src,'blob:new');
 assert.deepEqual(h.revoked,['blob:old']);
});
test('scroll controls preserve signed session and opposite directions',async()=>{
 const h=setup();await h.elements.down.onclick();await h.elements.up.onclick();
 const scroll=h.requests.filter(x=>x.path==='/recovery/scroll');
 assert.deepEqual(scroll.map(x=>JSON.parse(x.opts.body).dy),[450,-450]);
 assert.ok(scroll.every(x=>x.opts.headers.Authorization==='Bearer test-only-bearer'));
});
