// Exercise the real server route with fake authenticated identity and Tank transport.
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const root=process.cwd(), require=createRequire(path.join(root,'workspace/package.json')),ts=require('typescript');
let signedIn=true,fail=false,errorStatus=0,calls=[];
function load(file){
 const filename=path.join(root,file);
 const compiled=ts.transpileModule(readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText;
 const module={exports:{}};
 function local(name){
  if(name==='next/server')return {NextResponse:Response};
  if(name==='@/lib/tenant')return {currentTenant:async()=>{if(!signedIn)throw Error('placeholder-private-value');return {tenantId:'11111111-1111-4111-8111-111111111111',userId:'placeholder-user'}}};
  if(name==='../tank')return {homeRegion:()=> 'us-east',zenas:async(route,init)=>{
   calls.push({route,init,body:JSON.parse(init.body)});
   if(errorStatus)return Response.json({detail:'Room unavailable'},{status:errorStatus});
   if(fail)throw Error('https://placeholder.invalid/?token=placeholder-private-value');
   return Response.json(route==='matters'?{matter:{id:'placeholder-matter'}}:{ok:true,state:'released',memo:{text:'Civil rule',citations:['Fed. R. Civ. P. 1'],sources:[{citation:'Fed. R. Civ. P. 1',url:'https://example.invalid/rule'}]},rule_card:{elements:[{citation:'Fed. R. Civ. P. 1',quote:'Official text'}]}});
  }};
  if(name==='../rules')return load('workspace/app/api/zenas/rules.ts');
  return require(name);
 }
 vm.runInThisContext('(function(require,module,exports){'+compiled+'\n})',{filename})(local,module,module.exports);
 return module.exports;
}
const route=load('workspace/app/api/zenas/answers/route.ts');
const request=(body,key='11111111-1111-4111-8111-111111111111')=>new Request('https://placeholder.invalid/api/zenas/answers',{method:'POST',headers:{'content-type':'application/json','idempotency-key':key},body:JSON.stringify(body)});
for(const question of ['',{},[],4,'a'.repeat(4001)]){
 const response=await route.POST(request({question}));assert.equal(response.status,400);
}
assert.equal(calls.length,0);
signedIn=false;assert.equal((await route.POST(request({question:'Civil question'}))).status,401);assert.equal(calls.length,0);signedIn=true;
const body={question:'Civil question',tenant_id:'22222222-2222-4222-8222-222222222222',userId:'forged-user'};
const result=await (await route.POST(request(body))).json();assert.equal(result.kind,'answer');assert.equal(result.matterId,'placeholder-matter');assert.equal(result.sources[0].url,'https://example.invalid/rule');
assert(calls.every(call=>call.body.tenant_id==='11111111-1111-4111-8111-111111111111'));
assert.equal(calls[0].body.created_by,'placeholder-user');
const keys=calls.map(call=>call.init.headers['idempotency-key']);assert.notEqual(keys[0],keys[1]);
await route.POST(request(body));assert.deepEqual(calls.slice(2).map(call=>call.init.headers['idempotency-key']),keys);
errorStatus=503;assert.equal((await route.POST(request(body))).status,503);errorStatus=0;
fail=true;const error=await (await route.POST(request(body))).text();assert(!error.includes('placeholder-private-value'));assert(error.includes('Zenas could not answer'));
console.log('Authenticated server journey, tenant binding, receipts, retry identities and error redaction passed');
