import assert from 'node:assert/strict';
import test from 'node:test';
import {request} from 'node:http';
import {chmodSync,mkdirSync,mkdtempSync,readFileSync,realpathSync,rmSync,symlinkSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {DatabaseSync} from 'node:sqlite';
import {configuration,createRequestService,host,port} from './request-service.mjs';
import {openOfficialInbox} from './official-inbox.mjs';
import {canonical,sha,validateOneStop} from './one-stop-envelope.mjs';

const version='2025-06-18';
const payload={schemaVersion:'ngs.npc-one-stop.v1',projectId:'binari',intent:'기획 요청 내용입니다',character:{
 npcId:'npc.fixture',name:'기획 캐릭터',slug:'fixture',role:'마을 안내인',actId:'story.main.act01',chapterId:'chapter01'},
 sourceRefs:['기획 근거'],includeCombat:false};
const input=(key='submit-key',body=payload)=>({sourceId:'local',requestType:'npc.workflow',idempotencyKey:key,payload:body});

function folder(t){
 const parent=mkdtempSync(join(realpathSync(tmpdir()),'official-request-fixture-'));
 t.after(()=>rmSync(parent,{recursive:true,force:true}));
 const directory=join(parent,'official');
 return {directory,path:join(directory,'official-requests.sqlite')};
}
function read(path,sql='SELECT * FROM official_requests'){
 const db=new DatabaseSync(path,{readOnly:true});
 try{return db.prepare(sql).all().map(r=>({...r}));}
 finally{db.close();}
}
async function listener(t,directory){
 const server=createRequestService(configuration({REQUEST_OFFICIAL_INBOX_DIR:directory}));
 await new Promise(ok=>server.listen(0,host,ok));
 t.after(()=>new Promise(ok=>server.close(ok)));
 const origin='http://'+host+':'+server.address().port;
 const headers={Accept:'application/json, text/event-stream','Content-Type':'application/json','MCP-Protocol-Version':version};
 const post=(msg,{headers:custom={},path='/mcp'}={})=>fetch(origin+path,{method:'POST',headers:{...headers,...custom},body:JSON.stringify(msg)});
 const rpc=async(method,params,id=1)=>await (await post({jsonrpc:'2.0',id,method,params})).json();
 const submit=args=>rpc('tools/call',{name:'request_submit',arguments:args});
 const cancel=args=>rpc('tools/call',{name:'request_cancel',arguments:args});
 const list=args=>rpc('tools/call',{name:'request_list',arguments:args});
 const workflow=args=>rpc('tools/call',{name:'request_workflow_list',arguments:args});
 const update=arguments_=>rpc('tools/call',{name:'request_update_status',arguments:arguments_});
 return {origin,post,rpc,submit,cancel,list,workflow,update,headers};
}
function seedLegacy({directory,path}){
 mkdirSync(directory,{mode:0o700});writeFileSync(path,'',{mode:0o600});
 const db=new DatabaseSync(path);
 try{
  db.exec(`CREATE TABLE official_npc_one_stop(
   official_request_id TEXT NOT NULL UNIQUE, ngs_id TEXT NOT NULL, project_id TEXT NOT NULL,
   source_request_id INTEGER NOT NULL CHECK(source_request_id>0), request_type TEXT NOT NULL,
   schema_version TEXT NOT NULL, payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
   state TEXT NOT NULL CHECK(state='registered'), registered_at INTEGER NOT NULL,
   PRIMARY KEY(ngs_id,project_id,source_request_id));`);
  const body=canonical(payload);
  db.prepare('INSERT INTO official_npc_one_stop VALUES(?,?,?,?,?,?,?,?,?,?)').run(
   'request.npc.onestop.'+sha(canonical(['request.npc.onestop.v1','local','binari',1])).slice(0,40),
   'local','binari',1,'npc.workflow',payload.schemaVersion,body,sha(body),'registered',77);
 }finally{db.close();}
}

test('loopback config is fixed, dedicated DB path required and filesystem kept private',t=>{
 const {directory,path}=folder(t);
 assert.equal(host,'127.0.0.1');assert.equal(port,8770);
 assert.throws(()=>configuration({}),/PRIVATE_REQUIRED/);
 assert.throws(()=>configuration({REQUEST_OFFICIAL_INBOX_DIR:'relative'}),/PRIVATE_REQUIRED/);
 const config=configuration({REQUEST_OFFICIAL_INBOX_DIR:directory,REQUEST_NGS_REGISTRY_FILE:'/not/read',NGS_REQUEST_POLL_SECONDS:'invalid'});
 assert.deepEqual(config,{directory});
 openOfficialInbox(directory).close();
 chmodSync(path,0o644);
 assert.throws(()=>openOfficialInbox(directory),/PRIVATE_REQUIRED/);
 chmodSync(path,0o600);
 symlinkSync(directory,directory+'-link');
 assert.throws(()=>openOfficialInbox(directory+'-link'),/PRIVATE_REQUIRED/);
});

test('typed One-Stop input rejects unsafe expansions and permits the original combat fields',()=>{
 for(const bad of [
  {...payload,schemaVersion:'ngs.npc-one-stop.v2',fullRefresh:{confirmed:true}},
  {...payload,operation:'run'},{...payload,projectId:'other'},
  {...payload,character:{...payload.character,role:'  '}},{...payload,sourceRefs:[]},
  {...payload,includeCombat:true},{...payload,combatMotionPresets:['sword']},
  {...payload,motionPresets:{walk:'other',run:'run'}},
 ])assert.throws(()=>validateOneStop(bad,'binari'),/PAYLOAD_INVALID/);
 assert.doesNotThrow(()=>validateOneStop({...payload,includeCombat:true,combatMotionPresets:['sword-attack']},'binari'));
});

test('additive push ledger leaves exact prior table / rows untouched; deterministic replay survives restart',t=>{
 const files=folder(t);
 seedLegacy(files);
 const before=read(files.path,'SELECT * FROM official_npc_one_stop');
 const sql=read(files.path,"SELECT sql FROM sqlite_master WHERE name='official_npc_one_stop'");
 let inbox=openOfficialInbox(files.directory,{now:()=>100});
 const receipt=inbox.register(input('1'));
 assert.equal(receipt.status,'registered');
 assert.match(receipt.officialRequestId,/^request\.official\.[a-f0-9]{64}$/);
 assert.notEqual(receipt.officialRequestId,before[0].official_request_id);
 inbox.close();
 inbox=openOfficialInbox(files.directory,{now:()=>200});
 try{
  assert.deepEqual(inbox.register(input('1')),receipt);
  assert.throws(()=>inbox.register(input('1',{...payload,intent:'기획 수정 내용입니다'})),/IDEMPOTENCY_CONFLICT/);
  assert.throws(()=>inbox.register(input('second',{...payload,character:{...payload.character,npcId:'npc.other',slug:'FIXTURE'}})),/NPC_ALREADY_REGISTERED/);
  assert.throws(()=>inbox.register(input('other',{...payload,character:{...payload.character,slug:'other',npcId:'NPC.FIXTURE'}})),/NPC_ALREADY_REGISTERED/);
  const row=read(files.path)[0];
  assert.equal(read(files.path).length,1);assert.equal(row.registered_at,100);
  assert.equal(row.payload_sha256,sha(canonical(payload)));
  assert.deepEqual(read(files.path,'SELECT * FROM official_npc_one_stop'),before);
  assert.deepEqual(read(files.path,"SELECT sql FROM sqlite_master WHERE name='official_npc_one_stop'"),sql);
 }finally{inbox.close();}
});

test('tool discovery via genuine standard MCP, no registration by notification; HTTP receipt follows commit',async t=>{
 const {directory,path}=folder(t);
 const {origin,rpc,submit,post}=await listener(t,directory);
 const init=await rpc('initialize',{protocolVersion:version,capabilities:{},clientInfo:{name:'fixture',version:'1'}});
 assert.equal(init.result.protocolVersion,version);assert.deepEqual(init.result.capabilities,{tools:{}});
 assert.equal((await post({jsonrpc:'2.0',method:'notifications/initialized'})).status,202);
 const listing=await rpc('tools/list',{});
 assert.deepEqual(listing.result.tools.map(r=>r.name),['request_submit','request_cancel','request_list','request_workflow_list','request_update_status']);
 assert.equal((await fetch(origin+'/mcp')).status,405);
 assert.equal((await fetch(origin+'/api/requests/pending')).status,404);
 const before=await post({jsonrpc:'2.0',method:'tools/call',params:{name:'request_submit',arguments:input()}});
 assert.equal(before.status,202);assert.deepEqual(read(path),[]);
 const first=await submit(input());
 const receipt=first.result.structuredContent;
 assert.equal(first.result.isError,false);
 assert.deepEqual(Object.keys(receipt).sort(),['officialRequestId','status']);
 assert.deepEqual(JSON.parse(first.result.content[0].text),receipt);
 assert.equal(receipt.status,'registered');
 const row=read(path)[0];assert.equal(row.official_request_id,receipt.officialRequestId);
 assert.equal(row.state,'registered');assert.equal(row.request_type,'npc.workflow');
 assert.deepEqual((await submit(input())).result.structuredContent,receipt);
 assert.equal(read(path).length,1);
});

test('scope, browser traffic, bad payload and conflicting retries fail without duplicate work',async t=>{
 const {directory,path}=folder(t);
 const {post,submit,origin}=await listener(t,directory);
 const msg={jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'request_submit',arguments:input()}};
 for(const headers of [{Origin:origin},{Cookie:'sid=fixture'},{'Sec-Fetch-Site':'same-origin'}, {'X-Forwarded-For':'127.0.0.1'}]){
  assert.equal((await post(msg,{headers})).status,403);
 }
 const forged=await new Promise((resolve,reject)=>{
  request(origin+'/mcp',{headers:{Host:'forged.local'}},r=>{r.resume();resolve(r.statusCode);}).on('error',reject).end();
 });
 assert.equal(forged,403);
 assert.equal((await post(msg,{path:'/mcp?x=1'})).status,404);
 assert.equal((await post(msg,{headers:{'MCP-Protocol-Version':'other'}})).status,400);
 const bad=await submit({...input(),sourceId:'other'});
 assert.equal(bad.result.isError,true);
 assert.deepEqual(bad.result.structuredContent,{error:'REQUEST_SUBMIT_INVALID',status:400});
 const badPayload=await submit(input('submit-key',{...payload,sourceRefs:[]}));
 assert.deepEqual(badPayload.result.structuredContent,{error:'NGS_ONE_STOP_PAYLOAD_INVALID',status:400});
 assert.deepEqual(read(path),[]);
 const good=await submit(input());
 assert.equal(good.result.isError,false);
 const mismatch=await submit(input('submit-key',{...payload,intent:'다른 내용입니다'}));
 assert.deepEqual(mismatch.result.structuredContent,{error:'OFFICIAL_REQUEST_IDEMPOTENCY_CONFLICT',status:409});
 assert.deepEqual((await submit(input('other-key'))).result.structuredContent,{error:'OFFICIAL_REQUEST_NPC_ALREADY_REGISTERED',status:409});
 assert.equal(read(path).length,1);
});


function seedInstalledPush(path){
 const db=new DatabaseSync(path);
 try{
  // Exact installed 13de45f table and indexes, before any cancellation exists.
  db.exec(`CREATE TABLE IF NOT EXISTS official_requests(
   official_request_id TEXT NOT NULL UNIQUE,
   source_id TEXT NOT NULL,
   project_id TEXT NOT NULL,
   idempotency_key TEXT NOT NULL,
   request_type TEXT NOT NULL,
   schema_version TEXT NOT NULL,
   payload_json TEXT NOT NULL,
   payload_sha256 TEXT NOT NULL,
   state TEXT NOT NULL CHECK(state='registered'),
   registered_at INTEGER NOT NULL,
   PRIMARY KEY(source_id,project_id,idempotency_key)
  );
  CREATE UNIQUE INDEX IF NOT EXISTS official_requests_new_npc_id
    ON official_requests(project_id,json_extract(payload_json,'$.character.npcId') COLLATE NOCASE)
    WHERE request_type='npc.workflow' AND schema_version='ngs.npc-one-stop.v1';
  CREATE UNIQUE INDEX IF NOT EXISTS official_requests_new_npc_slug
    ON official_requests(project_id,json_extract(payload_json,'$.character.slug') COLLATE NOCASE)
    WHERE request_type='npc.workflow' AND schema_version='ngs.npc-one-stop.v1';`);
  const args=key=>['request.official.'+sha(canonical(['request.official.v1','local','binari',key])),
   'local','binari',key,'npc.workflow',payload.schemaVersion];
  const insert=db.prepare('INSERT INTO official_requests(rowid,official_request_id,source_id,project_id,idempotency_key,request_type,schema_version,payload_json,payload_sha256,state,registered_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)');
  const raw=canonical(payload);
  insert.run(11,...args('old-key'),raw,sha(raw),'registered',83);
  const other=canonical({...payload,character:{...payload.character,slug:'other',npcId:'npc.other'}});
  insert.run(27,...args('another-key'),other,sha(other),'registered',95);
 }finally{db.close();}
}

test('installed 13de45f schema migrates atomically; exact official cancellation preserves independent rows',async t=>{
 const files=folder(t);
 seedLegacy(files);seedInstalledPush(files.path);
 const sql='SELECT rowid,* FROM official_requests ORDER BY rowid';
 const before=read(files.path,sql);
 const legacy=read(files.path,'SELECT * FROM official_npc_one_stop');
 const {post,cancel,submit,origin}=await listener(t,files.directory);
 assert.deepEqual(read(files.path,sql),before);
 assert.deepEqual(read(files.path,'SELECT * FROM official_npc_one_stop'),legacy);
 const officialRequestId=before[0].official_request_id;
 const ready={officialRequestId,status:'cancelled'};
 const args={officialRequestId};
 assert.equal((await post({jsonrpc:'2.0',method:'tools/call',params:{name:'request_cancel',arguments:args}})).status,202);
 assert.deepEqual(read(files.path,sql),before);
 const rpc={jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'request_cancel',arguments:args}};
 assert.equal((await post(rpc,{headers:{Origin:origin}})).status,403);
 assert.deepEqual((await cancel({...args,sourceId:'local'})).result.structuredContent,{error:'REQUEST_CANCEL_INVALID',status:400});
 assert.deepEqual((await cancel({officialRequestId:'request.official.'+'a'.repeat(64)})).result.structuredContent,
  {error:'OFFICIAL_REQUEST_NOT_FOUND',status:404});
 assert.deepEqual((await cancel(args)).result.structuredContent,ready);
 const after=[{...before[0],state:'cancelled'},before[1]];
 assert.deepEqual(read(files.path,sql),after);
 assert.deepEqual((await cancel(args)).result.structuredContent,ready);
 assert.deepEqual(read(files.path,sql),after);
 assert.deepEqual((await submit(input('old-key'))).result.structuredContent,
  {error:'OFFICIAL_REQUEST_IDEMPOTENCY_CONFLICT',status:409});
 assert.deepEqual(read(files.path,sql),after);
 const again=(await submit(input('new-key'))).result.structuredContent;
 assert.equal(again.status,'registered');assert.notEqual(again.officialRequestId,officialRequestId);
 assert.deepEqual((await cancel(args)).result.structuredContent,ready);
 assert.equal(read(files.path,sql)[2].state,'registered');
 assert.deepEqual(read(files.path,'SELECT * FROM official_npc_one_stop'),legacy);
 // Opening selected schema a second time does not re-migrate or touch any state.
 const afterReapply=read(files.path,sql);
 openOfficialInbox(files.directory).close();
 assert.deepEqual(read(files.path,sql),afterReapply);
});

test('unknown attached schema refuses the upgrade without altering old table or rows',t=>{
 const files=folder(t);
 seedLegacy(files);seedInstalledPush(files.path);
 const db=new DatabaseSync(files.path);
 db.exec('CREATE INDEX unexpected ON official_requests(state)');
 db.close();
 const readBefore=read(files.path,'SELECT rowid,* FROM official_requests');
 const sql=read(files.path,"SELECT sql FROM sqlite_schema WHERE tbl_name='official_requests' ORDER BY name");
 assert.throws(()=>openOfficialInbox(files.directory),/SCHEMA_UNSUPPORTED/);
 assert.deepEqual(read(files.path,'SELECT rowid,* FROM official_requests'),readBefore);
 assert.deepEqual(read(files.path,"SELECT sql FROM sqlite_schema WHERE tbl_name='official_requests' ORDER BY name"),sql);
});

const replacement=(npcId='npc.fixture-replacement',slug='fixture-replacement')=>({
 ...payload,schemaVersion:'ngs.npc-one-stop.replace.v1',
 character:{...payload.character,npcId,slug,name:'Test fixture',actId:'act01'},
});
test('same-ID replacement is registered and cancellable through the same tools with no legacy action',async t=>{
 const files=folder(t);
 seedLegacy(files);seedInstalledPush(files.path);
 openOfficialInbox(files.directory).close(); // Start from the deployed cancellation schema.
 const schemas=()=>read(files.path,"SELECT type, name, sql FROM sqlite_schema ORDER BY name");
 const initialSchema=schemas(),oldRows=read(files.path,'SELECT * FROM official_requests');
 const {submit,cancel,rpc}=await listener(t,files.directory);
 const list=await rpc('tools/list',{});
 assert.deepEqual(list.result.tools.map(t=>t.name),['request_submit','request_cancel','request_list','request_workflow_list','request_update_status']);
 assert.deepEqual(list.result.tools[0].inputSchema.oneOf[0].properties.payload.oneOf[0].properties.schemaVersion.enum,[
  'ngs.npc-one-stop.v1','ngs.npc-one-stop.replace.v1',
 ]);
 const body=replacement();
 const {result}=await submit(input('replace-fixture',body));
 assert.equal(result.isError,false);
 const receipt=result.structuredContent;
 assert.equal(receipt.status,'registered');
 assert.deepEqual((await submit(input('replace-fixture',body))).result.structuredContent,receipt);
 const rows=read(files.path,'SELECT * FROM official_requests ORDER BY rowid');
 assert.deepEqual(rows.slice(0,2),oldRows);
 assert.equal(rows[2].schema_version,'ngs.npc-one-stop.replace.v1');
 assert.equal(JSON.parse(rows[2].payload_json).character.npcId,'npc.fixture-replacement');
 assert.equal(JSON.parse(rows[2].payload_json).character.slug,'fixture-replacement');
 assert.deepEqual(schemas(),initialSchema);
 assert.deepEqual((await cancel({officialRequestId:receipt.officialRequestId})).result.structuredContent,{...receipt,status:'cancelled'});
 assert.deepEqual((await submit(input('replace-fixture',body))).result.structuredContent,
  {error:'OFFICIAL_REQUEST_IDEMPOTENCY_CONFLICT',status:409});
 assert.equal((await submit(input('replace-fixture-fresh',body))).result.structuredContent.status,'registered');
});

test('both intake directions enforce registered target uniqueness and exact replacement shape',async t=>{
 const {directory,path}=folder(t);
 const {submit}=await listener(t,directory);
 const conflict={error:'OFFICIAL_REQUEST_NPC_ALREADY_REGISTERED',status:409};
 const bad={error:'NGS_ONE_STOP_PAYLOAD_INVALID',status:400};
 for(const body of [
  replacement('other.seojin'),replacement('npc...seojin'),replacement('npc.fixture','../invalid'),
  {...replacement(),fullRefresh:{confirmed:true}}, {...replacement(),projectId:'other'},
 ])assert.deepEqual((await submit(input('bad',body))).result.structuredContent,bad);
 assert.equal((await submit(input('new-one'))).result.structuredContent.status,'registered');
 assert.deepEqual((await submit(input('replace-id',replacement('npc.fixture','elsewhere')))).result.structuredContent,conflict);
 assert.deepEqual((await submit(input('replace-slug',replacement('character.fixture','fixture')))).result.structuredContent,conflict);
 assert.equal((await submit(input('replace-first',replacement('character.historic','historic')))).result.structuredContent.status,'registered');
 const other=(npcId,slug)=>({...payload,character:{...payload.character,npcId,slug}});
 assert.deepEqual((await submit(input('new-id',other('character.historic','historic')))).result.structuredContent,bad);
 assert.deepEqual((await submit(input('new-slug',other('npc.another','Historic')))).result.structuredContent,conflict);
 assert.deepEqual((await submit(input('new-one',{...payload,schemaVersion:'ngs.npc-one-stop.replace.v1'}))).result.structuredContent,
  {error:'OFFICIAL_REQUEST_IDEMPOTENCY_CONFLICT',status:409});
 assert.equal(read(path).length,2);
});

test('request_list returns only the nine summary fields, reads both states, and supports status filters',async t=>{
 const files=folder(t);
 seedLegacy(files);
 const inbox=openOfficialInbox(files.directory,{now:()=>1700000000000});
 let first,second;
 try{
  first=inbox.register(input('first'));
  second=inbox.register(input('second',replacement()));
  inbox.cancel({officialRequestId:first.officialRequestId});
 }finally{inbox.close();}
 const before=read(files.path,'SELECT rowid,* FROM official_requests');
 const schema=read(files.path,"SELECT type,name,sql FROM sqlite_schema ORDER BY name");
 const {list,rpc,post,origin}=await listener(t,files.directory);
 const tools=(await rpc('tools/list',{})).result.tools;
 assert.equal(tools.find(t=>t.name==='request_list').annotations.readOnlyHint,true);
 const out=(await list({})).result.structuredContent;
 const summary=(receipt,body,status)=>({officialRequestId:receipt.officialRequestId,sourceId:'local',
  projectId:'binari',requestType:'npc.workflow',schemaVersion:body.schemaVersion,
  status,registeredAt:1700000000000,targetNpcId:body.character.npcId,targetName:body.character.name});
 assert.deepEqual(out,{requests:[summary(second,replacement(),'registered'),summary(first,payload,'cancelled')],nextCursor:null});
 assert.deepEqual((await list({status:'registered'})).result.structuredContent,{requests:[out.requests[0]],nextCursor:null});
 assert.deepEqual((await list({status:'cancelled'})).result.structuredContent,{requests:[out.requests[1]],nextCursor:null});
 assert.equal((await post({jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'request_list',arguments:{}}},
  {headers:{Origin:origin}})).status,403);
 for(const args of [{status:'queued'},{status:null},{limit:0},{limit:101},{limit:1.1},{cursor:'not.a.cursor'},
                    {cursor:null},{includePayload:true},null,[]]){
  const result=(await list(args)).result;
  assert.equal(result.isError,true);
  assert.deepEqual(result.structuredContent,{error:'REQUEST_LIST_INVALID',status:400});
 }
 assert.deepEqual(read(files.path,'SELECT rowid,* FROM official_requests'),before);
 assert.deepEqual(read(files.path,"SELECT type,name,sql FROM sqlite_schema ORDER BY name"),schema);
});

test('request_list keyset order stays stable across ties and excludes inserts after the first page',async t=>{
 const {directory,path}=folder(t);
 const targets=Array.from({length:53},(_,i)=>({
  ...payload,character:{...payload.character,npcId:'npc.fixture'+i,slug:'fixture'+i,name:'Fixture '+i},
 }));
 let acceptAt=1700000000000;
 const store=openOfficialInbox(directory,{now:()=>acceptAt});
 const old=targets.map((body,i)=>store.register(input('fixture-key'+i,body)).officialRequestId);
 store.close();
 const {list}=await listener(t,directory);
 const first=(await list({status:'registered'})).result.structuredContent;
 assert.equal(first.requests.length,50);
 assert.deepEqual(first.requests.map(row=>row.officialRequestId),[...old].reverse().slice(0,50));
 assert.equal(typeof first.nextCursor,'string');
 // A later insertion with a clock that moves backward would otherwise sneak into page two.
 acceptAt--;
 const later=openOfficialInbox(directory,{now:()=>acceptAt});
 const added=later.register(input('after-horizon',replacement())).officialRequestId;
 later.close();
 const second=(await list({status:'registered',limit:100,cursor:first.nextCursor})).result.structuredContent;
 assert.deepEqual(second.requests.map(row=>row.officialRequestId),[...old].reverse().slice(50));
 assert.equal(second.nextCursor,null);
 assert.deepEqual((await list({cursor:first.nextCursor})).result.structuredContent,{error:'REQUEST_LIST_INVALID',status:400});
 const fresh=(await list({limit:100})).result.structuredContent;
 assert.deepEqual(fresh.requests.map(row=>row.officialRequestId),[...old].reverse().concat(added));
 assert.equal(read(path).length,54);
});

test('workflow list returns hash-verified registered payloads only; cursor has a separate scope',async t=>{
 const {directory,path}=folder(t);
 const inbox=openOfficialInbox(directory,{now:()=>1700000000000});
 let first,second;
 try{
  first=inbox.register(input('planning-first'));
  second=inbox.register(input('planning-second',replacement()));
 }finally{inbox.close();}
 const {workflow,list,update,rpc}=await listener(t,directory);
 const before=read(path,'SELECT rowid,* FROM official_requests');
 const tools=(await rpc('tools/list',{})).result.tools;
 assert.equal(tools.find(t=>t.name==='request_workflow_list').annotations.readOnlyHint,false);
 const head=(await workflow({limit:1})).result.structuredContent;
 assert.deepEqual(head.requests,[{officialRequestId:second.officialRequestId,projectId:'binari',requestType:'npc.workflow',
  schemaVersion:replacement().schemaVersion,payload:replacement(),payloadSha256:sha(canonical(replacement())),status:'registered',
  registeredAt:1700000000000}]);
 assert.equal(typeof head.nextCursor,'string');
 const tail=(await workflow({cursor:head.nextCursor})).result.structuredContent;
 assert.equal(tail.requests[0].officialRequestId,first.officialRequestId);
 assert.equal(tail.nextCursor,null);
 const wrong={error:'REQUEST_WORKFLOW_LIST_INVALID',status:400};
 assert.deepEqual((await workflow({status:'registered'})).result.structuredContent,wrong);
 assert.deepEqual((await list({cursor:head.nextCursor})).result.structuredContent,{error:'REQUEST_LIST_INVALID',status:400});
 const ops=(await list({limit:1})).result.structuredContent;
 assert.deepEqual((await workflow({cursor:ops.nextCursor})).result.structuredContent,wrong);
 assert.deepEqual(read(path,'SELECT rowid,* FROM official_requests'),before);
 assert.equal((await update({officialRequestId:second.officialRequestId,status:'planned'})).result.structuredContent.status,'planned');
 assert.deepEqual((await workflow({})).result.structuredContent.requests.map(r=>r.officialRequestId),[first.officialRequestId]);
 const db=new DatabaseSync(path);
 try{db.prepare("UPDATE official_requests SET payload_sha256=? WHERE official_request_id=?").run('a'.repeat(64),first.officialRequestId);}
 finally{db.close();}
 assert.deepEqual((await workflow({})).result.structuredContent,{error:'OFFICIAL_REQUEST_WORKFLOW_LIST_FAILED',status:503});
});

test('status changes are exact, idempotent and preserve active uniqueness and terminal records',async t=>{
 const {directory,path}=folder(t);
 const {submit,update,cancel,list,workflow}=await listener(t,directory);
 const first=(await submit(input('first'))).result.structuredContent;
 const id=first.officialRequestId;
 const initial=read(path)[0],state=async status=>(await update({officialRequestId:id,status})).result.structuredContent;
 const conflict={error:'OFFICIAL_REQUEST_STATUS_CONFLICT',status:409};
 assert.deepEqual(await state('failed'),conflict);
 assert.deepEqual(await state('completed'),conflict);
 assert.deepEqual(await state('registered'),{error:'REQUEST_STATUS_UPDATE_INVALID',status:400});
 assert.deepEqual(await state('planned'),{officialRequestId:id,status:'planned'});
 assert.deepEqual(await state('planned'),{officialRequestId:id,status:'planned'});
 assert.deepEqual(read(path)[0],{...initial,state:'planned'});
 assert.deepEqual((await cancel({officialRequestId:id})).result.structuredContent,{error:'OFFICIAL_REQUEST_CANCEL_CONFLICT',status:409});
 assert.deepEqual((await submit(input('second',replacement('npc.fixture','other')))).result.structuredContent,
  {error:'OFFICIAL_REQUEST_NPC_ALREADY_REGISTERED',status:409});
 assert.deepEqual(await state('completed'),{officialRequestId:id,status:'completed'});
 assert.deepEqual(await state('completed'),{officialRequestId:id,status:'completed'});
 assert.deepEqual(await state('failed'),conflict);
 assert.deepEqual(await state('planned'),conflict);
 const second=(await submit(input('second',replacement('npc.fixture','fixture')))).result.structuredContent;
 assert.equal(second.status,'registered');
 const next=status=>update({officialRequestId:second.officialRequestId,status});
 assert.equal((await next('planned')).result.structuredContent.status,'planned');
 assert.equal((await next('failed')).result.structuredContent.status,'failed');
 assert.equal((await next('failed')).result.structuredContent.status,'failed');
 assert.deepEqual((await next('completed')).result.structuredContent,conflict);
 const third=(await submit(input('third'))).result.structuredContent;
 await cancel({officialRequestId:third.officialRequestId});
 assert.deepEqual((await update({officialRequestId:third.officialRequestId,status:'planned'})).result.structuredContent,conflict);
 assert.deepEqual((await submit(input('third'))).result.structuredContent,{error:'OFFICIAL_REQUEST_IDEMPOTENCY_CONFLICT',status:409});
 const stored=read(path);
 assert.deepEqual(stored.map(row=>row.state),['completed','failed','cancelled']);
 for(const status of ['planned','completed','failed','cancelled']){
  const rows=(await list({status})).result.structuredContent.requests;
  assert.deepEqual(rows.map(r=>r.officialRequestId),stored.filter(r=>r.state===status).map(r=>r.official_request_id));
 }
 assert.deepEqual((await workflow({})).result.structuredContent,{requests:[],nextCursor:null});
 const foreign='request.official.'+'e'.repeat(64);
 const db=new DatabaseSync(path);
 try{db.prepare("INSERT INTO official_requests VALUES(?,?,?,?,?,?,?,?,?,?)").run(foreign,'other','binari','foreign',
      'other.type','foreign.schema','{}',sha('{}'),'registered',17);}
 finally{db.close();}
 assert.deepEqual((await update({officialRequestId:foreign,status:'planned'})).result.structuredContent,
  {error:'OFFICIAL_REQUEST_NOT_FOUND',status:404});
 assert.equal(read(path).at(-1).state,'registered');
 assert.deepEqual((await workflow({})).result.structuredContent,{requests:[],nextCursor:null});
});

const conceptPayload={schemaVersion:'ngs.concept-category-workflow.v1',projectId:'binari',operation:'add',
 categoryId:'category.add.'+'a'.repeat(32),groupId:'world',title:'기획 카테고리',outline:'범위와 목차',requirements:'지켜야 할 규칙',requiredSections:['범위','규칙']};
const conceptInput=(key,body=conceptPayload)=>({sourceId:'local',requestType:'concept.category.workflow',idempotencyKey:key,payload:body});
const {requiredSections:defaultSections,...legacyAddPayload}=conceptPayload;
const conceptExisting=(operation,categoryId='concept.test-fixture')=>({...legacyAddPayload,operation,categoryId,groupId:null,
 ...(['migrate','remove'].includes(operation)?{outline:'',requirements:''}:operation==='update'?{outline:''}:{})});

test('exact concept envelopes reject mismatched families, unsafe placement, legacy extras and malformed text',async t=>{
 const {directory,path}=folder(t),{submit,rpc}=await listener(t,directory);
 const schema=(await rpc('tools/list',{})).result.tools[0].inputSchema;
 assert.deepEqual(schema.properties.requestType.enum,['npc.workflow','concept.category.workflow','npc.planning.update','npc.planning.add','audio.generate','audio.delete','audio.separate','motion-preset.convert-2d','npc.animation.regenerate','map.generate','map.update']);
 assert.deepEqual(schema.oneOf[1].properties.payload.required,['schemaVersion','projectId','operation','categoryId','groupId','title','outline','requirements']);
 const expected={error:'NGS_CONCEPT_CATEGORY_PAYLOAD_INVALID',status:400};
 for(const bad of [
  legacyAddPayload, {...conceptPayload,requiredSections:[]}, {...conceptPayload,requiredSections:['둘','둘']},
  {...conceptPayload,requiredSections:[' 둘']}, {...conceptPayload,requiredSections:['A\nB']},
  {...conceptPayload,requiredSections:['글'.repeat(81)]}, {...conceptExisting('update'),requiredSections:defaultSections},
  {...conceptPayload,categoryId:'../world'}, {...conceptPayload,groupId:null}, {...conceptPayload,groupId:'../../group'},
  {...conceptPayload,groupId:'world\n'}, {...conceptPayload,title:' title '}, {...conceptPayload,outline:''},
  {...conceptPayload,requirements:'𠀋'.repeat(4001)}, {...conceptPayload,requirements:'\ud800'},
  {...conceptPayload,operation:'delete'}, {...conceptPayload,schemaVersion:'ngs.npc-one-stop.v1'},
  conceptExisting('replace'), {...conceptExisting('replace'),groupId:'world'}, {...conceptExisting('replace'),outline:'  '},
  {...conceptExisting('update'),outline:'no'}, {...conceptExisting('update'),requirements:''}, {...conceptExisting('update'),groupId:'world'},
  {...conceptExisting('migrate'),outline:'not permitted'}, {...conceptExisting('remove'),requirements:'not permitted'},
  {...conceptExisting('remove'),directDelete:true}, {...conceptExisting('migrate'),categoryId:'world\n'},
  {...conceptExisting('migrate'),categoryId:'world:extra'}, {...conceptPayload,title:'two\nlines'},
  {...conceptPayload,outline:'unsafe\u0000text'},
  payload,
 ])assert.deepEqual((await submit(conceptInput('invalid',bad))).result.structuredContent,expected);
 assert.deepEqual((await submit(input('cross',conceptPayload))).result.structuredContent,{error:'NGS_ONE_STOP_PAYLOAD_INVALID',status:400});
 assert.deepEqual(read(path),[]);
});

test('concept and NPC share formal tools without crossing duplicate scope or changing the deployed schema',async t=>{
 const {directory,path}=folder(t);
 openOfficialInbox(directory).close();
 const schemaSql="SELECT type,name,sql FROM sqlite_schema ORDER BY name";
 const originalSchema=read(path,schemaSql),{submit,cancel,list,workflow,update}=await listener(t,directory);
 const dup={error:'OFFICIAL_REQUEST_CONCEPT_CATEGORY_ALREADY_REGISTERED',status:409};
 const add=(await submit(conceptInput('add'))).result.structuredContent;
 assert.deepEqual((await submit(conceptInput('add',{...conceptPayload}))).result.structuredContent,add);
 assert.deepEqual((await submit(conceptInput('different-add'))).result.structuredContent,dup);
 assert.deepEqual((await submit(conceptInput('add',{...conceptPayload,title:'다른 제목'}))).result.structuredContent,
  {error:'OFFICIAL_REQUEST_IDEMPOTENCY_CONFLICT',status:409});
 // An identical human-readable target does not collide across families.
 const body=conceptExisting('update',payload.character.npcId);
 const both=await Promise.all([submit(input('npc')),submit(conceptInput('category',body)),submit(conceptInput('competing',body))]);
 const value=both.map(row=>row.result.structuredContent);
 assert.equal(value[0].status,'registered');
 assert.equal(value.slice(1).filter(v=>v.status==='registered').length,1);
 assert.equal(value.slice(1).filter(v=>v.error===dup.error).length,1);
 const replace=value.slice(1).find(v=>v.status==='registered');
 const cursor=(await workflow({limit:1})).result.structuredContent;
 const later=(await workflow({cursor:cursor.nextCursor})).result.structuredContent;
 const feed=[...cursor.requests,...later.requests];
 assert.equal(feed.length,3);
 assert.deepEqual(feed.find(row=>row.officialRequestId===replace.officialRequestId).payload,body);
 assert.equal(feed.find(row=>row.officialRequestId===replace.officialRequestId).payloadSha256,sha(canonical(body)));
 const summary=(await list({})).result.structuredContent.requests.find(row=>row.officialRequestId===replace.officialRequestId);
 assert.equal(summary.requestType,'concept.category.workflow');assert.equal(summary.targetNpcId,body.categoryId);
 assert.equal(summary.targetName,body.title);assert.equal(Object.hasOwn(summary,'payload'),false);
 assert.equal((await update({...replace,status:'planned'})).result.structuredContent.status,'planned');
 assert.deepEqual((await submit(conceptInput('conflicting-remove',conceptExisting('remove',body.categoryId)))).result.structuredContent,dup);
 assert.equal((await update({...replace,status:'completed'})).result.structuredContent.status,'completed');
 const remove=(await submit(conceptInput('remove',conceptExisting('remove',body.categoryId)))).result.structuredContent;
 assert.equal(remove.status,'registered');
 assert.equal((await cancel({officialRequestId:remove.officialRequestId})).result.structuredContent.status,'cancelled');
 const migrate=(await submit(conceptInput('migrate',conceptExisting('migrate',body.categoryId)))).result.structuredContent;
 assert.equal(migrate.status,'registered');
 assert.equal((await workflow({})).result.structuredContent.requests.some(row=>row.officialRequestId===remove.officialRequestId),false);
 assert.deepEqual(read(path,schemaSql),originalSchema);
 const fileRows=read(path);
 assert.equal(fileRows.find(row=>row.official_request_id===remove.officialRequestId).state,'cancelled');
 assert.equal(fileRows.find(row=>row.official_request_id===replace.officialRequestId).state,'completed');
});

test('unsupported historical registered concept replace is failed once; source and stale replay cannot revive it',async t=>{
 const {directory,path}=folder(t);
 openOfficialInbox(directory).close();
 const body=conceptExisting('replace'),key='historical-replace',id='request.official.'+sha(canonical(['request.official.v1','local','binari',key]));
 const raw=canonical(body), db=new DatabaseSync(path);
 try{db.prepare(`INSERT INTO official_requests(official_request_id,source_id,project_id,idempotency_key,request_type,schema_version,
  payload_json,payload_sha256,state,registered_at) VALUES(?,?,?,?,?,?,?,?,'registered',?)`)
  .run(id,'local','binari',key,'concept.category.workflow',body.schemaVersion,raw,sha(raw),1);}
 finally{db.close();}
 const {submit,workflow,update,list}=await listener(t,directory);
 const result=async(p)=>(await p).result.structuredContent;
 const [before]=(await result(list({status:'registered'}))).requests;
 assert.equal(before.officialRequestId,id);assert.equal(Object.hasOwn(before,'failureReason'),false);
 assert.deepEqual(await result(workflow({})),{requests:[],nextCursor:null});
 assert.deepEqual(await result(submit(conceptInput(key,body))),{error:'OFFICIAL_REQUEST_IDEMPOTENCY_CONFLICT',status:409});
 assert.deepEqual(await result(submit(conceptInput('new-destructive',body))),{error:'NGS_CONCEPT_CATEGORY_PAYLOAD_INVALID',status:400});
 assert.deepEqual(await result(workflow({})),{requests:[],nextCursor:null});
 assert.deepEqual(await result(update({officialRequestId:id,status:'planned'})),{error:'OFFICIAL_REQUEST_STATUS_CONFLICT',status:409});
 const [failed]=(await result(list({status:'failed'}))).requests;
 assert.equal(failed.officialRequestId,id);assert.equal(failed.failureReason,'REQUEST_UNSUPPORTED_RECREATE');
 const [stored]=read(path);
 assert.equal(stored.payload_json,raw);assert.equal(stored.payload_sha256,sha(raw));assert.equal(stored.state,'failed');
});

test('historical exact add without section list remains replayable, listable and transitions unchanged',async t=>{
 const {directory,path}=folder(t);
 openOfficialInbox(directory).close();
 const body=legacyAddPayload,key='historical-add',id='request.official.'+sha(canonical(['request.official.v1','local','binari',key]));
 const raw=canonical(body), db=new DatabaseSync(path);
 try{db.prepare(`INSERT INTO official_requests(official_request_id,source_id,project_id,idempotency_key,request_type,schema_version,
  payload_json,payload_sha256,state,registered_at) VALUES(?,?,?,?,?,?,?,?,'registered',?)`)
  .run(id,'local','binari',key,'concept.category.workflow',body.schemaVersion,raw,sha(raw),1);}
 finally{db.close();}
 const {submit,workflow,update}=await listener(t,directory);
 const out=async p=>(await p).result.structuredContent;
 assert.deepEqual(await out(submit(conceptInput(key,body))),{officialRequestId:id,status:'registered'});
 assert.deepEqual(await out(submit(conceptInput('fresh-without-sections',body))),{error:'NGS_CONCEPT_CATEGORY_PAYLOAD_INVALID',status:400});
 assert.deepEqual((await out(workflow({}))).requests[0].payload,body);
 assert.equal((await out(update({officialRequestId:id,status:'planned'}))).status,'planned');
 assert.equal((await out(update({officialRequestId:id,status:'completed'}))).status,'completed');
});

test('2D motion request registration is typed, retains original and guards separate active result IDs',async t=>{
 const {directory,path}=folder(t),{submit,list,workflow,cancel,rpc}=await listener(t,directory);
 const requestType='motion-preset.convert-2d';
 const args={sourceId:'local',requestType,idempotencyKey:'motion-plan',payload:{schemaVersion:'ngs.motion-preset-convert-2d.v1',
  projectId:'binari',sourcePresetId:'Walk.3D',variantPresetId:'walk-sprite-2d',label:'걷기 2D'}};
 const bad=[
  {sourcePresetId:'../walk'},{sourcePresetId:'Walk-Sprite-2D'},{variantPresetId:'Walk-2d'},{variantPresetId:'w'},
  {variantPresetId:'a'.repeat(65)},{variantPresetId:'walk.2d'},{label:'한\n줄'},{label:'two\u2028lines'},{label:' 과한 '},
  {label:'\ud800'},{label:'🎶'.repeat(121)},{projectId:'other'},{schemaVersion:'wrong'},{outputPath:'/tmp/motion'},
 ];
 for(const overrides of bad){
  const res=(await submit({...args,payload:{...args.payload,...overrides}})).result;
  assert.equal(res.isError,true);
  assert.deepEqual(res.structuredContent,{error:'NGS_MOTION_PRESET_CONVERT_2D_PAYLOAD_INVALID',status:400});
 }
 assert.deepEqual(read(path),[]);
 const tools=(await rpc('tools/list',{})).result.tools;
 const advertised=tools.find(tool=>tool.name==='request_submit').inputSchema.oneOf.find(option=>option.properties.requestType.const===requestType);
 assert.equal(advertised.properties.payload.properties.variantPresetId.pattern,'^[a-z][a-z0-9-]{1,63}$');
 const registered=(await submit(args)).result.structuredContent;
 assert.equal(registered.status,'registered');
 assert.deepEqual((await submit(args)).result.structuredContent,registered);
 assert.deepEqual((await submit({...args,payload:{...args.payload,label:'변경'}})).result.structuredContent,
  {error:'OFFICIAL_REQUEST_IDEMPOTENCY_CONFLICT',status:409});
 assert.deepEqual((await submit({...args,idempotencyKey:'different',payload:{...args.payload,sourcePresetId:'run'}})).result.structuredContent,
  {error:'OFFICIAL_MOTION_2D_VARIANT_ALREADY_REGISTERED',status:409});
 const summary=(await list({})).result.structuredContent.requests[0];
 assert.equal(summary.targetNpcId,args.payload.variantPresetId);assert.equal(summary.targetName,args.payload.label);
 const actual=(await workflow({})).result.structuredContent.requests[0];
 assert.equal(actual.officialRequestId,registered.officialRequestId);
 assert.equal(actual.payloadSha256,sha(canonical(args.payload)));assert.deepEqual(actual.payload,args.payload);
 assert.deepEqual((await cancel({officialRequestId:registered.officialRequestId})).result.structuredContent,
  {officialRequestId:registered.officialRequestId,status:'cancelled'});
 assert.equal((await submit({...args,idempotencyKey:'after-cancel',payload:{...args.payload,label:'🎶'.repeat(120)}})).result.structuredContent.status,'registered');
});

test('only correctly bound registered unknown types, obsolete schemas and canonical unsupported combat retire only on paged intake',async t=>{
 const {directory,path}=folder(t);
 openOfficialInbox(directory).close();
 const officialSchema=()=>read(path,"SELECT type,name,sql FROM sqlite_schema WHERE tbl_name='official_requests' ORDER BY name");
 const beforeSchema=officialSchema(),cases=[];
 function add(key,opts={}){
  const {type='npc.workflow',project='binari',source='local',state='registered',body=payload,invalidDigest=false,invalidId=false}=opts;
  const schema=opts.schema??body.schemaVersion;
  const id='request.official.'+sha(canonical(['request.official.v1',source,project,key+(invalidId?'-wrong':'')]));
  const raw=canonical(body);
  cases.push({id,key,type,project,source,state,schema,raw,digest:invalidDigest?'0'.repeat(64):sha(raw)});
  return id;
 }
 const old={schemaVersion:'ngs.retired-work.v1',projectId:'binari',revision:42};
 const unknown=add('retired-type',{type:'retired.workflow',body:old});
 const obsolete=add('retired-schema',{body:{...old,schemaVersion:'ngs.npc-one-stop.minimum.v0'}});
 const spaceKnown=add('old-space-combat',{source:'space',body:{...payload,character:{...payload.character,npcId:'npc.space',slug:'spacem'},
  includeCombat:true,combatMotionPresets:['strike']}});
 const legit=add('supported-pending',{body:replacement()});
 const motion=add('supported-motion',{type:'motion-preset.convert-2d',body:{schemaVersion:'ngs.motion-preset-convert-2d.v1',
  projectId:'binari',sourcePresetId:'walk',variantPresetId:'walk-2d',label:'걷기 2D'}});
 add('already-planned',{type:'retired.workflow',body:old,state:'planned'});
 add('already-completed',{type:'retired.workflow',body:old,state:'completed'});
 add('already-failed',{type:'retired.workflow',body:old,state:'failed'});
 add('already-cancelled',{type:'retired.workflow',body:old,state:'cancelled'});
 add('digest-mismatch',{type:'retired.workflow',body:old,invalidDigest:true});
 add('id-mismatch',{type:'retired.workflow',body:old,invalidId:true});
 add('schema-mismatch',{type:'retired.workflow',body:old,schema:'ngs.retired-work.v2'});
 add('foreign-project',{type:'retired.workflow',body:old,project:'other'});
 add('foreign-source',{type:'retired.workflow',body:old,source:'external'});
 const db=new DatabaseSync(path);
 try{
  const insert=db.prepare('INSERT INTO official_requests VALUES(?,?,?,?,?,?,?,?,?,?)');
  cases.forEach((r,i)=>insert.run(r.id,r.source,r.project,r.key,r.type,r.schema,r.raw,r.digest,r.state,i));
 }finally{db.close();}
 const marked=[unknown,obsolete,spaceKnown];
 const app=openOfficialInbox(directory);
 try{
  assert.equal(read(path,'SELECT * FROM official_request_terminal_reasons').length,0);
  assert.equal(read(path).filter(x=>marked.includes(x.official_request_id)&&x.state==='registered').length,3);
  assert.throws(()=>app.updateStatus({officialRequestId:legit,status:'completed'}),/OFFICIAL_REQUEST_STATUS_CONFLICT/);
  let cursor=null;const emitted=[];
  do{
   const page=app.workflowList(cursor===null?{limit:1}:{limit:1,cursor});
   emitted.push(...page.requests);
   cursor=page.nextCursor;
  }while(cursor!==null);
  assert.deepEqual(emitted.map(x=>x.officialRequestId),[motion,legit]);
 }finally{app.close();}
 openOfficialInbox(directory).close(); // Restart does not rescan or clear reasons.
 assert.deepEqual(officialSchema(),beforeSchema);
 assert.deepEqual(read(path,'SELECT official_request_id FROM official_request_terminal_reasons ORDER BY official_request_id'),
  marked.toSorted().map(official_request_id=>({official_request_id})));
 assert.deepEqual(read(path).map(row=>[row.official_request_id,row.state,row.payload_json,row.payload_sha256]),
  cases.map(r=>[r.id,marked.includes(r.id)?'failed':r.state,r.raw,r.digest]));
});

test('fresh unsupported combat cannot create a stuck registration; a valid request can use a fresh key',async t=>{
 const {directory,path}=folder(t),{submit,workflow}=await listener(t,directory);
 const combat={...payload,includeCombat:true,combatMotionPresets:['strike']};
 const result=async(p)=>(await p).result.structuredContent;
 assert.deepEqual(await result(submit(input('new-combat',combat))),{error:'REQUEST_UNSUPPORTED_RECREATE',status:422});
 assert.deepEqual(read(path),[]);
 const receipt=await result(submit(input('new-supported')));
 assert.equal(receipt.status,'registered');
 assert.equal((await result(workflow({}))).requests[0].officialRequestId,receipt.officialRequestId);
});

test('new exact selected-preset schemas register immutable payloads and share old/new NPC target fences',async t=>{
 const {directory,path}=folder(t),{submit,workflow,cancel}=await listener(t,directory);
 const selected={schemaVersion:'ngs.npc-one-stop.selection.v1',projectId:'binari',
  intent:payload.intent,character:payload.character,sourceRefs:payload.sourceRefs,
  motionSelection:[{presetId:'attack-flat',posePreset:'combat',assetSetSha256:'1'.repeat(64)},
   {presetId:'run-2d',posePreset:'idle',assetSetSha256:'a'.repeat(64)}]};
 const [first,second]=selected.motionSelection;
 const wire=async args=>(await submit(args)).result.structuredContent;
 for(const body of [{...selected,schemaVersion:'ngs.npc-one-stop.selection.replace.v1',motionSelection:[]},{...selected,includeCombat:false},
  {...selected,motionPresets:{walk:'walk',run:'run'}},{...selected,combatMotionPresets:['attack-flat']},
  {...selected,motionSelection:[first,{...second,presetId:'ATTACK-FLAT'}]},
  ...[{presetId:'..'},{presetId:'evil/name'},{presetId:'run-2d\n'},{posePreset:'walk'},
   {assetSetSha256:'A'.repeat(64)},{assetSetSha256:'a'.repeat(63)},{hash:'extra'}].map(change=>({...selected,motionSelection:[{...first,...change}]})),
  {...selected,schemaVersion:'ngs.npc-one-stop.selection.replace.v1',character:{...selected.character,npcId:'wrong'}}]){
   assert.deepEqual(await wire(input('bad-selection',body)),{error:'NGS_ONE_STOP_PAYLOAD_INVALID',status:400});
 }
 assert.deepEqual(read(path),[]);
 const created=await wire(input('selected',selected));
 assert.equal(created.status,'registered');assert.deepEqual(await wire(input('selected',selected)),created);
 assert.deepEqual(await wire(input('selected',{...selected,motionSelection:[second,first]})),
  {error:'OFFICIAL_REQUEST_IDEMPOTENCY_CONFLICT',status:409});
 assert.deepEqual(await wire(input('legacy',payload)),{error:'OFFICIAL_REQUEST_NPC_ALREADY_REGISTERED',status:409});
 const feed=(await workflow({})).result.structuredContent.requests[0];
 assert.equal(feed.officialRequestId,created.officialRequestId);assert.deepEqual(feed.payload,selected);
 assert.equal(feed.payloadSha256,sha(canonical(selected)));
 await cancel({officialRequestId:created.officialRequestId});
 const legacy=await wire(input('legacy',payload));assert.equal(legacy.status,'registered');
 const replace={...selected,schemaVersion:'ngs.npc-one-stop.selection.replace.v1'};
 assert.deepEqual(await wire(input('selected-replacement',replace)),{error:'OFFICIAL_REQUEST_NPC_ALREADY_REGISTERED',status:409});
 await cancel({officialRequestId:legacy.officialRequestId});
 assert.equal((await wire(input('selected-replacement',replace))).status,'registered');
});

test('formal NPC animation regeneration is durable and excludes concurrent same-NPC whole requests',async t=>{
 const {directory}=folder(t);
 const api=await listener(t,directory);
 const schemaVersion='ngs.npc-animation.regenerate.v1',requestType='npc.animation.regenerate';
 const regen={schemaVersion,projectId:'binari',npcId:'npc.fixture',motionSelection:[
  {presetId:'run-2d',posePreset:'idle',assetSetSha256:'a'.repeat(64)},
  {presetId:'sword-attack-flat',posePreset:'combat',assetSetSha256:'b'.repeat(64)},
 ]};
 const submit=(idempotencyKey,value)=>api.submit({sourceId:'local',requestType,idempotencyKey,payload:value});
 for(const malformed of [
  {...regen,npcId:'../npc.fixture'},{...regen,includeCombat:true},{...regen,motionSelection:[]},
  {...regen,motionSelection:[regen.motionSelection[0],{...regen.motionSelection[0],presetId:'RUN-2D'}]},
 ]){
  const bad=await submit('bad',malformed);
  assert.equal(bad.result.structuredContent.error,'NGS_NPC_ANIMATION_REGENERATE_PAYLOAD_INVALID');
 }
 const accepted=(await submit('regen',regen)).result.structuredContent;
 assert.match(accepted.officialRequestId,/^request\.official\.[a-f0-9]{64}$/u);
 assert.deepEqual((await submit('regen',regen)).result.structuredContent,accepted);
 assert.equal((await submit('another',regen)).result.structuredContent.error,'OFFICIAL_REQUEST_NPC_ALREADY_REGISTERED');
 assert.equal((await api.submit(input('whole'))).result.structuredContent.error,'OFFICIAL_REQUEST_NPC_ALREADY_REGISTERED');
 const target=(await api.list({})).result.structuredContent.requests[0];
 assert.equal(target.targetNpcId,'npc.fixture');assert.equal(target.targetName,'애니메이션 생성');
 const projected=(await api.workflow({})).result.structuredContent.requests[0];
 assert.equal(projected.requestType,requestType);assert.deepEqual(projected.payload,regen);
 assert.equal((await api.update({officialRequestId:accepted.officialRequestId,status:'planned'})).result.structuredContent.status,'planned');
 assert.equal((await api.update({officialRequestId:accepted.officialRequestId,status:'completed'})).result.structuredContent.status,'completed');
 const whole=(await api.submit(input('whole'))).result.structuredContent;
 assert.match(whole.officialRequestId,/^request\.official\./u);
 assert.equal((await submit('blocked-by-whole',regen)).result.structuredContent.error,'OFFICIAL_REQUEST_NPC_ALREADY_REGISTERED');
 const legacy=(await submit('existing-import',{...regen,npcId:'character.yeonwoo'})).result.structuredContent;
 assert.match(legacy.officialRequestId,/^request\.official\./u);
});

test('full replacement retains the exact legacy identity and both selected routes accept larger byte-bounded lists',async t=>{
 const {directory}=folder(t);
 const {submit,workflow,rpc}=await listener(t,directory);
 const motions=Array.from({length:20},(_,i)=>({presetId:'fixture-motion-'+i,
  posePreset:i%2?'combat':'idle',assetSetSha256:'a'.repeat(64)}));
 const body={schemaVersion:'ngs.npc-one-stop.selection.replace.v1',projectId:'binari',
  intent:payload.intent,character:{...payload.character,npcId:'character.seojin',slug:'seojin',actId:'act01'},
  sourceRefs:payload.sourceRefs,motionSelection:motions};
 const send=async args=>(await submit(args)).result.structuredContent;
 for(const character of [
  {...body.character,slug:'yeonwoo'},{...body.character,npcId:'character.Seojin'},
  {...body.character,actId:'act02'},{...body.character,chapterId:'chapter06'},
 ]){
  assert.deepEqual(await send(input('blocked',{...body,character})),
   {error:'NGS_ONE_STOP_PAYLOAD_INVALID',status:400});
 }
 assert.deepEqual(await send(input('new-legacy',{...body,schemaVersion:'ngs.npc-one-stop.selection.v1'})),
  {error:'NGS_ONE_STOP_PAYLOAD_INVALID',status:400});
 const huge=Array.from({length:320},(_,i)=>({...motions[0],presetId:'long-'+String(i).padStart(3,'0')+'x'.repeat(114)}));
 assert(Buffer.byteLength(canonical({...body,motionSelection:huge}))>64*1024);
 assert.deepEqual(await send(input('too-big',{...body,motionSelection:huge})),
  {error:'NGS_ONE_STOP_PAYLOAD_INVALID',status:400});
 const regen={schemaVersion:'ngs.npc-animation.regenerate.v1',projectId:'binari',npcId:'character.yeonwoo',motionSelection:motions};
 const regenSend=async (idempotencyKey,body)=>send({sourceId:'local',requestType:'npc.animation.regenerate',idempotencyKey,payload:body});
 assert.deepEqual(await regenSend('too-big-regen',{...regen,motionSelection:huge}),
  {error:'NGS_NPC_ANIMATION_REGENERATE_PAYLOAD_INVALID',status:400});
 const receipt=await send(input('legacy-replace',body)),regenReceipt=await regenSend('legacy-animation',regen);
 assert.equal(receipt.status,'registered');assert.equal(regenReceipt.status,'registered');
 const rows=(await workflow({})).result.structuredContent.requests;
 assert.deepEqual(rows.find(r=>r.officialRequestId===receipt.officialRequestId).payload,body);
 assert.deepEqual(rows.find(r=>r.officialRequestId===regenReceipt.officialRequestId).payload,regen);
 const schema=(await rpc('tools/list',{})).result.tools.find(x=>x.name==='request_submit').inputSchema.oneOf;
 assert.equal(schema[0].properties.payload.oneOf[1].properties.motionSelection.maxItems,undefined);
 assert.equal(schema.find(x=>x.properties.requestType.const==='npc.animation.regenerate').properties.payload.properties.motionSelection.maxItems,undefined);
});

test('v2 scoped appearance contracts admit full/animation-only, and older versions share target collision guards',async t=>{
 const {directory}=folder(t),api=await listener(t,directory);
 const fixture=name=>JSON.parse(readFileSync(new URL('../contracts/'+name,import.meta.url)));
 const full=fixture('npc-one-stop-motion-selection.v2.fixture.json').cases[0].submit;
 const regen=fixture('npc-animation-regenerate.v2.fixture.json').submit;
 const wire=async args=>(await api.submit(args)).result.structuredContent;
 const accepted=await wire(full);assert.equal(accepted.status,'registered');
 const conflict={error:'OFFICIAL_REQUEST_NPC_ALREADY_REGISTERED',status:409};
 assert.deepEqual(await wire({...full,idempotencyKey:'old-full',payload:{...full.payload,schemaVersion:'ngs.npc-one-stop.selection.v1'}}),conflict);
 assert.deepEqual(await wire({...regen,idempotencyKey:'regen-on-active-full',payload:{...regen.payload,npcId:full.payload.character.npcId}}),conflict);
 const animated=await wire(regen);assert.equal(animated.status,'registered');
 assert.deepEqual(await wire({...regen,idempotencyKey:'old-regen',payload:{...regen.payload,schemaVersion:'ngs.npc-animation.regenerate.v1'}}),conflict);
 assert.deepEqual(await wire({...full,idempotencyKey:'whole-on-active-regen',payload:{...full.payload,
  schemaVersion:'ngs.npc-one-stop.selection.replace.v2',character:{...full.payload.character,npcId:regen.payload.npcId}}}),conflict);
 const {requests}= (await api.workflow({})).result.structuredContent;
 assert.deepEqual(requests.find(x=>x.officialRequestId===accepted.officialRequestId).payload,full.payload);
 assert.deepEqual(requests.find(x=>x.officialRequestId===animated.officialRequestId).payload,regen.payload);
});

test('exact empty selection registers a new NPC without creating a replacement',async t=>{
 const {directory}=folder(t),{submit,workflow}=await listener(t,directory);
 const schemaVersion='ngs.npc-one-stop.selection.v1';
 const {intent,character,sourceRefs}=payload;
 const request={schemaVersion,projectId:'binari',intent,character,sourceRefs,motionSelection:[]};
 const receipt=(await submit(input('portrait-only',request))).result.structuredContent;
 assert.equal(receipt.status,'registered');
 const rows=(await workflow({})).result.structuredContent.requests;
 assert.deepEqual(rows[0].payload,request);
});

test('basic combat opt-in registers as distinct from unsupported historic combat',async t=>{
 const {directory}=folder(t),{submit,workflow}=await listener(t,directory);
 const schemaVersion='ngs.npc-one-stop.basic.v1';
 const {intent,character,sourceRefs}=payload;
 const request={schemaVersion,projectId:'binari',intent,character:{...character,actId:'act01'},sourceRefs,includeCombat:true};
 const receipt=(await submit(input('basic-original',request))).result.structuredContent;
 assert.equal(receipt.status,'registered');
 const rows=(await workflow({})).result.structuredContent.requests;
 assert.deepEqual(rows[0].payload,request);
});

test('pinned prop file.delete advertises and admits exactly one folder only when PDS is ready',async t=>{
 const {directory,path}=folder(t);
 let eligible=false;
 const service=createRequestService({directory,fileDeleteReadiness:async()=>eligible});
 await new Promise(resolve=>service.listen(0,host,resolve));
 t.after(()=>new Promise(resolve=>service.close(resolve)));
 const rpc=async(method,params={})=>await (await fetch('http://'+host+':'+service.address().port+'/mcp',{
  method:'POST',headers:{Accept:'application/json, text/event-stream','Content-Type':'application/json'},
  body:JSON.stringify({jsonrpc:'2.0',id:1,method,params})})).json();
 const submit=payload=>rpc('tools/call',{name:'request_submit',arguments:{sourceId:'local',requestType:'file.delete',idempotencyKey:'one-prop',payload}});
 const visible=async()=>(await rpc('tools/list')).result.tools[0].inputSchema.properties.requestType.enum.includes('file.delete');
 const target={root:'canonical-files/binari/props',path:'lantern',kind:'directory'};
 const expected={revision:2,sha256:'a'.repeat(64)};
 const body={schemaVersion:'ngs.file-delete.v1',projectId:'binari',target,expected};
 assert.equal(await visible(),false);
 assert.match(JSON.stringify(await submit(body)),/NGS_FILE_DELETE_UNAVAILABLE/);
 assert.equal(read(path).length,0);
 eligible=true;
 assert.equal(await visible(),true);
 for(const malformed of [{...body,target:[target]},{...body,target:{...target,path:'../lantern'}},{...body,target:{...target,path:'lantern\n'}},
  {...body,target:{...target,root:'canonical-files/binari/npcs'}},{...body,expected:null},{...body,targets:[target]}])
   assert.match(JSON.stringify(await submit(malformed)),/NGS_FILE_DELETE_PAYLOAD_INVALID/);
 const accepted=await submit(body);
 assert.equal(accepted.result.structuredContent.status,'registered');
 assert.equal(read(path).length,1);
 const updated=await rpc('tools/call',{name:'request_update_status',arguments:{
  officialRequestId:accepted.result.structuredContent.officialRequestId,status:'planned'}});
 assert.equal(updated.result.isError,false);
 const completed=await rpc('tools/call',{name:'request_update_status',arguments:{
  officialRequestId:accepted.result.structuredContent.officialRequestId,status:'completed'}});
 assert.equal(completed.result.isError,false);
 const list=(await rpc('tools/call',{name:'request_list',arguments:{limit:25}})).result;
 assert.equal(list.isError,false);
 assert.deepEqual(list.structuredContent.requests.map(x=>[x.requestType,x.status,x.targetNpcId,x.targetName]),
  [['file.delete','completed','lantern','폴더 삭제']]);
});

test('standalone NPC deletion capability is separate from pinned props',async t=>{
 const {directory,path}=folder(t);
 const npcRoot='canonical-files/binari/npcs';
 const service=createRequestService({directory,fileDeleteReadiness:async root=>root===npcRoot});
 await new Promise(resolve=>service.listen(0,host,resolve));
 t.after(()=>new Promise(resolve=>service.close(resolve)));
 const rpc=async(method,params={})=>await (await fetch('http://'+host+':'+service.address().port+'/mcp',{
  method:'POST',headers:{Accept:'application/json, text/event-stream','Content-Type':'application/json'},
  body:JSON.stringify({jsonrpc:'2.0',id:1,method,params})})).json();
 const submit=payload=>rpc('tools/call',{name:'request_submit',arguments:{sourceId:'local',requestType:'file.delete',idempotencyKey:'one-npc',payload}});
 const target={root:npcRoot,path:'npc.lantern',kind:'directory'};
 const body={schemaVersion:'ngs.file-delete.v1',projectId:'binari',target,expected:{creationTaskId:'workflow.task.'+'a'.repeat(64)}};
 const listed=JSON.stringify((await rpc('tools/list')).result);
 assert.match(listed,/creationTaskId/);
 assert.doesNotMatch(listed,/canonical-files\/binari\/props/);
 assert.match(JSON.stringify(await submit({...body,expected:{}})),/NGS_FILE_DELETE_PAYLOAD_INVALID/);
 assert.match(JSON.stringify(await submit({...body,target:{...target,root:'canonical-files/binari/props',path:'lantern'},expected:{revision:1,sha256:'b'.repeat(64)}})),/NGS_FILE_DELETE_UNAVAILABLE/);
 assert.equal((await submit(body)).result.structuredContent.status,'registered');
 assert.equal(read(path).length,1);
});

test('local map request contracts are discoverable and the valid queued payload reaches planner intake',async t=>{
 const {directory}=folder(t),api=await listener(t,directory);
 const advertised=(await api.rpc('tools/list',{})).result.tools.find(v=>v.name==='request_submit').inputSchema;
 for(const [type,schema] of [['map.generate','ngs.map-generate.v1'],['map.update','ngs.map-update.v1']]){
  assert.ok(advertised.properties.requestType.enum.includes(type));
  assert.equal(advertised.oneOf.find(x=>x.properties.requestType.const===type).properties.payload.properties.schemaVersion.const,schema);
 }
 const graph={title:'Ritual yard',startRegionId:'first',
  regions:['first','second'].map((id,i)=>({id,name:id,description:'Area',backgroundId:'background.'+String(i+1).repeat(64),imageSha256:'a'.repeat(64)})),
  connections:[{id:'route',from:'first',to:'second',hit:null,spawn:null,label:'Next'}]};
 const map={sourceId:'local',requestType:'map.generate',idempotencyKey:'map-queued-fixture',
  payload:{schemaVersion:'ngs.map-generate.v1',projectId:'binari',graph,intent:'Find visible pathways.'}};
 assert.deepEqual((await api.submit({...map,payload:{...map.payload,intent:''}})).result.structuredContent,
  {error:'NGS_MAP_PAYLOAD_INVALID',status:400});
 const out=(await api.submit(map)).result.structuredContent;
 assert.equal(out.status,'registered');
 assert.equal((await api.submit(map)).result.structuredContent.officialRequestId,out.officialRequestId);
 assert.ok((await api.workflow({limit:10})).result.structuredContent.requests.some(x=>x.officialRequestId===out.officialRequestId));
});
