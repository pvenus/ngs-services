import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {chmodSync,mkdtempSync,readFileSync,readdirSync,realpathSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createInterface} from 'node:readline';
import {pathToFileURL,fileURLToPath} from 'node:url';
import {crc32,deflateSync} from 'node:zlib';
import {test} from 'node:test';
import {PlannerStore,pollOnce} from '../workflow-planner/planner.mjs';
import {createRequestService} from '../request-service/request-service.mjs';
import {createRSClient,makeLoopbackTransport} from '../workflow-planner/rs.mjs';
import {sha} from '../workflow-planner/envelope.mjs';

const rootURL=new URL('../',import.meta.url);
const req=JSON.parse(readFileSync(new URL('docs/prop-design/create-request.json',rootURL)));
const payload={schemaVersion:'ngs.prop-image-generate.v1',projectId:'binari',propId:'candle',state:'lit',
 lane:'detail',intent:'초 심지 위에 작은 불꽃 하나',designContextSha256:'1'.repeat(64)};
const submit=(key,p)=>({sourceId:'local',requestType:'prop.image-generate',idempotencyKey:key,payload:p});

function png(alpha=true){
 const h=Buffer.alloc(13);h.writeUInt32BE(2);h.writeUInt32BE(1,4);h[8]=8;h[9]=6;
 const chunk=(tag,data)=>{const len=Buffer.alloc(4),crc=Buffer.alloc(4),body=Buffer.concat([Buffer.from(tag),data]);
  len.writeUInt32BE(data.length);crc.writeUInt32BE(crc32(body));return Buffer.concat([len,body,crc]);};
 return Buffer.concat([Buffer.from('89504e470d0a1a0a','hex'),chunk('IHDR',h),
  chunk('IDAT',deflateSync(Buffer.from([0,0,0,0,alpha?0:255,55,55,55,255]))),chunk('IEND',Buffer.alloc(0))]);
}
async function fixture(t,enabled,propImageReadiness){
 const root=mkdtempSync(join(realpathSync(tmpdir()),'prop-official-test-')),db=join(root,'planner.sqlite');
 const server=createRequestService({directory:join(root,'requests'),propImageEnabled:enabled,propImageReadiness});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const planner=new PlannerStore(db,{propImageEnabled:enabled});chmodSync(db,0o600);
 t.after(async()=>{planner.close();await new Promise(resolve=>server.close(resolve));rmSync(root,{recursive:true,force:true});});
 const transport=makeLoopbackTransport(server.address().port),rs=createRSClient({transport});
 const rpc=async(name,args)=>(await transport({jsonrpc:'2.0',id:1,method:'tools/call',params:{name,arguments:args}})).result;
 return {root,db,planner,rs,rpc,transport};
}
test('default RS cannot register prop or expose it to planner and explicit schema is strict',async t=>{
 const {rpc,planner}=await fixture(t,false);
 assert.deepEqual((await rpc('request_submit',submit('no',payload))).structuredContent,
  {error:'PROP_IMAGE_RUNTIME_UNAVAILABLE',status:503});
 assert.deepEqual((await rpc('request_submit',submit('unsafe',{...payload,model:'caller-controlled'}))).structuredContent,
  {error:'NGS_PROP_IMAGE_PAYLOAD_INVALID',status:400});
 assert.equal(planner.db.prepare('SELECT count(*) AS n FROM workflow_tasks').get().n,0);
});
test('capability denial does not persist a request and return of capability allows the same key',async t=>{
 let ready=false;
 const {rpc,transport}=await fixture(t,true,async()=>ready);
 const list=async()=>JSON.stringify((await transport({jsonrpc:'2.0',id:1,method:'tools/list'})).result);
 assert.equal((await list()).includes('prop.image-generate'),false);
 const input=submit('retry-when-capable',payload);
 assert.deepEqual((await rpc('request_submit',input)).structuredContent,
  {error:'PROP_IMAGE_RUNTIME_UNAVAILABLE',status:503});
 assert.deepEqual((await rpc('request_workflow_list',{})).structuredContent.requests,[]);
 ready=true;
 assert.equal((await list()).includes('prop.image-generate'),true);
 const created=(await rpc('request_submit',input)).structuredContent;
 assert.equal(created.status,'registered');
 ready=false;
 assert.equal((await rpc('request_workflow_list',{})).structuredContent.requests[0].officialRequestId,created.officialRequestId);
});
test('same request wire supports already-authorized Space relay without changing a local schema',async t=>{
 const {root}=await fixture(t,false);
 const q='fixture-space-prop';
 const server=createRequestService({directory:join(root,'site'),propImageEnabled:true,
  readSpaceRequest:async queueId=>({queueId,requestType:'prop.image-generate',idempotencyKey:q,payload})});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 try{
  const call=makeLoopbackTransport(server.address().port);
  const r=(await call({jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'request_submit_space',arguments:{queueId:q}}})).result;
  assert.equal(r.isError,false);assert.equal(r.structuredContent.status,'registered');
  const page=await createRSClient({transport:call}).call('request_workflow_list',{limit:8});
  assert.deepEqual(page.requests[0].payload,payload);assert.equal(page.requests[0].sourceId,'space');
 }finally{await new Promise(resolve=>server.close(resolve));}
});

test('official RS → Planner → resident Image → real PDS and Filex scoped writer → one published asset',
 {skip:!process.env.PROP_IMAGE_EXECUTION_WORKER_MODULE},async t=>{
 const {db,root,rpc,rs,planner}=await fixture(t,true);
 const category=(await rpc('request_submit',req)).structuredContent.officialRequestId;
 assert.equal((await pollOnce(planner,rs)).acknowledged,1);
 const row=planner.db.prepare('SELECT task_id,payload_sha256 FROM workflow_tasks JOIN workflow_runs USING(official_request_id)').get();
 const claimStamp=Date.now()+1;planner.db.prepare("UPDATE workflow_tasks SET status='running',updated_at=?").run(claimStamp);
 const claim={officialRequestId:category,taskId:row.task_id,projectId:'binari',categoryId:req.payload.categoryId,
  payloadSha256:row.payload_sha256,claimStamp};
 const child=spawn('python3',['-B',fileURLToPath(new URL('tests/prop-image-workflow-fixture.py',rootURL)),root],{
  env:{...process.env,PYTHONPATH:fileURLToPath(new URL('data-service/',rootURL))},stdio:['pipe','pipe','pipe']});
 let errors='';child.stderr.on('data',d=>{errors+=d;});
 const childDone=once(child,'close');
 t.after(async()=>{child.kill();await childDone;});
 child.stdin.end(JSON.stringify(claim));
 const lines=createInterface({input:child.stdout});
 const first=await Promise.race([once(lines,'line'),childDone.then(()=>{throw Error(errors);})]);
 const {port,context}=JSON.parse(first[0]);lines.close();
 const api='http://127.0.0.1:'+port+'/v1/props/ownership/workflow-image';
 const call=async(suffix='',body)=>fetch(api+suffix,body===undefined?{}:{
  method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const {runPropImageFromPath,nextPropImageFromPath}=await import(pathToFileURL(process.env.PROP_IMAGE_EXECUTION_WORKER_MODULE));
 assert.equal((await (await call()).json()).schemaVersion,'workflow.prop-image-write.v1');
 const inputs=[];
 const pds={readPropDesign:async claim=>{
  const read=await call('/read',{claim});if(!read.ok)throw Error('PDS_BAD_CONTEXT');return read.json();
 },publishPropImage:async(claim,artifact)=>{
  // Same claim, valid hash, but an opaque PNG must not be published.
  const opaque=png(false);
  const bad=await call('',{claim,artifact:{...artifact,base64:opaque.toString('base64'),sha256:sha(opaque),bytes:opaque.length}});
  assert.equal(bad.status,409);
  assert.equal((await call('',{claim:{...claim,claimStamp:claim.claimStamp+1},artifact})).status,403);
  const first=await call('',{claim,artifact});assert.equal(first.status,200,await first.clone().text());
  const exact=await first.json();
  assert.deepEqual(await (await call('',{claim,artifact})).json(),exact);
  return exact;
 }};
 const generate=async(req)=>{
  const created=(await rpc('request_submit',req)).structuredContent;
  assert.equal(created.status,'registered');
  assert.equal((await pollOnce(planner,rs)).acknowledged,1);
  const task=nextPropImageFromPath(db);assert.equal(task.officialRequestId,created.officialRequestId);
  return runPropImageFromPath({dbPath:db,pds,...task,renderProp:async input=>{inputs.push(input);return png();}});
 };
 const result=await generate(submit('render-prop',{...payload,designContextSha256:context.contextSha256}));
 assert.equal(result.state,'completed');
 assert.equal(result.resultRef,'media-artifacts/assets/'+sha(png())+'.png');
 assert.deepEqual(readFileSync(join(root,'filex-media','assets',sha(png())+'.png')),png());
 assert.equal(readdirSync(join(root,'filex-media','receipts')).length,1);
 assert.equal(inputs.length,1);assert.equal(inputs[0].intent,payload.intent);
 assert.deepEqual(inputs[0].sources,context.sources);
 assert.equal((await pollOnce(planner,rs)).completed,1);
 const stale=await generate(submit('stale-context',payload));
 assert.equal(stale.state,'failed');assert.equal(inputs.length,1);
});
