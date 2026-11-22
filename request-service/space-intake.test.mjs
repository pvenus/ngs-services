import assert from 'node:assert/strict';
import {test} from 'node:test';
import {mkdtempSync,rmSync,realpathSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createRequestService} from './request-service.mjs';
import {canonical,sha} from './one-stop-envelope.mjs';

const generated={schemaVersion:'ngs.audio-generate.v1',projectId:'binari',kind:'bgm',title:'Fixture music',conceptId:'category.sound',
 brief:'Quiet closing music',text:'',durationSeconds:null};
const deleted=officialRequestId=>({schemaVersion:'ngs.audio-delete.v1',projectId:'binari',officialRequestId});
const toolSubmit='request_submit_space';
const spaceQueue=(queueId,requestType,payload)=>({queueId,requestType,idempotencyKey:queueId,payload});
async function fixture(t,readSpaceRequest) {
 const root=mkdtempSync(join(realpathSync(tmpdir()),'rs-site-fixture-'));
 const server=createRequestService({directory:join(root,'requests'),readSpaceRequest});
 await new Promise(ok=>server.listen(0,'127.0.0.1',ok));
 t.after(async()=>{await new Promise(ok=>server.close(ok));rmSync(root,{recursive:true,force:true});});
 const port=server.address().port;
 async function rpc(method,params,headers={}) {
  const r=await fetch('http://127.0.0.1:'+port+'/mcp',{method:'POST',headers:{Accept:'application/json, text/event-stream',
   'Content-Type':'application/json',...headers},body:JSON.stringify({jsonrpc:'2.0',id:1,method,params})});
  return [r.status,await r.json()];
 }
 return {root,port,rpc,call:async(name,args)=>{const [,r]=await rpc('tools/call',{name,arguments:args});
  return r.result?.structuredContent??r.error;}};
}
test('Site tools are absent without the independently configured owner reader',async t=>{
 const {rpc,call}=await fixture(t);
 const [,listing]=await rpc('tools/list',{});
 assert.equal((await call(toolSubmit,{queueId:'site-audio-1'})).code,-32602);
});
test('verified Site MCP intake stays distinct from local; wrong authorization, mismatch and replay are fail-closed',async t=>{
 const snapshots=new Map([['site-audio-1',spaceQueue('site-audio-1','audio.generate',generated)]]);
 let reads=0;
 const {call,rpc}=await fixture(t,async id=>{reads++;return snapshots.get(id);});
 assert.equal((await call('request_submit',{sourceId:'space',requestType:'audio.generate',idempotencyKey:'local-1',payload:generated})).status,400);
 assert.equal((await call(toolSubmit,{queueId:'site-audio-1',payload:generated})).status,400);
 assert.equal(reads,0);
 const [http]=await rpc('tools/call',{name:toolSubmit,arguments:{queueId:'site-audio-1'}},{Origin:'https://example.test'});
 assert.equal(http,403); assert.equal(reads,0);
 assert.equal((await call(toolSubmit,{queueId:'site-missing'})).status,403);
 const submitted=await call(toolSubmit,{queueId:'site-audio-1'});
 assert.equal(submitted.status,'registered');
 const [feed]= (await call('request_workflow_list',{})).requests;
 assert.equal(feed.sourceId,'space');assert.equal(feed.officialRequestId,submitted.officialRequestId);
 const ops=(await call('request_list',{})).requests;
 assert.equal(ops[0].sourceId,'space');
 assert.equal((await call('request_cancel',{officialRequestId:submitted.officialRequestId})).status,404);
 for(const status of ['planned','completed'])
  assert.equal((await call('request_update_status',{officialRequestId:submitted.officialRequestId,status})).status,status);
 assert.deepEqual(await call(toolSubmit,{queueId:'site-audio-1'}),{...submitted,status:'completed'});
 snapshots.set('site-audio-1',spaceQueue('site-audio-1','audio.generate',{...generated,title:'changed'}));
 assert.equal((await call(toolSubmit,{queueId:'site-audio-1'})).status,409);
 const local=await call('request_submit',{sourceId:'local',requestType:'audio.generate',idempotencyKey:'local-1',payload:generated});
 assert.notEqual(submitted.officialRequestId,local.officialRequestId);
 snapshots.set('site-delete-1',spaceQueue('site-delete-1','audio.delete',deleted(submitted.officialRequestId)));
 assert.equal((await call(toolSubmit,{queueId:'site-delete-1'})).status,'registered');
 assert.equal((await call('request_submit',{sourceId:'local',requestType:'audio.delete',idempotencyKey:'delete-local',payload:deleted(submitted.officialRequestId)})).status,409);
});
test('real Request MCP and candidate Planner preserve Space provenance through completed status and deletion',async t=>{
 const planner=process.env.WORKFLOW_PLANNER_SITE_SOURCE??fileURLToPath(new URL('../workflow-planner',import.meta.url));
 const load=name=>import(pathToFileURL(join(planner,name)).href);
 const {PlannerStore,pollOnce}=await load('planner.mjs');
 const {createRSClient,makeLoopbackTransport}=await load('rs.mjs');
 const snapshots=new Map([['site-audio-1',spaceQueue('site-audio-1','audio.generate',generated)]]);
 const {root,port,call}=await fixture(t,async id=>snapshots.get(id));
 const store=new PlannerStore(join(root,'planner.sqlite')); let current=store; t.after(()=>current.close());
 const rs=createRSClient({transport:makeLoopbackTransport(port)});
 const registration=await call(toolSubmit,{queueId:'site-audio-1'});
 const [spaceFeed]=(await call('request_workflow_list',{})).requests;
 assert.equal(spaceFeed.sourceKey,'site-audio-1');
 assert.throws(()=>store.stage({...spaceFeed,sourceKey:'space.bgm.'+'0'.repeat(64)}),/WORKFLOW_ROW_INVALID/);
 assert.equal((await pollOnce(store,rs)).acknowledged,1);
 const run=store.db.prepare('SELECT * FROM workflow_runs').get();
 assert.ok(!Object.hasOwn(run,'source_id'));assert.equal(run.state,'planned');assert.equal(run.official_request_id,registration.officialRequestId);
 store.db.prepare("UPDATE workflow_tasks SET status='completed' WHERE official_request_id=?").run(run.official_request_id);
 assert.equal((await pollOnce(store,rs)).completed,1);
 snapshots.set('site-delete-1',spaceQueue('site-delete-1','audio.delete',deleted(run.official_request_id)));
 const removed=await call(toolSubmit,{queueId:'site-delete-1'});
 assert.equal(removed.status,'registered');
 assert.equal((await pollOnce(store,rs)).acknowledged,1);
 const task=store.db.prepare('SELECT task_type,task_input FROM workflow_tasks WHERE official_request_id=?').get(removed.officialRequestId);
 assert.equal(task.task_type,'audio.delete');
 assert.equal(JSON.parse(task.task_input).target.path,'canonical-files/binari/audio/'+run.official_request_id);
 store.db.prepare("UPDATE workflow_tasks SET status='completed' WHERE official_request_id=?").run(removed.officialRequestId);
 assert.equal((await pollOnce(store,rs)).completed,1);
 // Exact inherited legacy code, not a special rollback build, opens unchanged DB.
 const legacyPath=process.env.WORKFLOW_PLANNER_SITE_BASE;
 if(!legacyPath)return;
 const legacy=await import(pathToFileURL(join(legacyPath,'planner.mjs')).href);
 const before=store.db.prepare("SELECT sql FROM sqlite_schema WHERE type='table' ORDER BY name").all();
 const history=store.db.prepare('SELECT * FROM workflow_runs ORDER BY official_request_id').all();
 store.close();current=new legacy.PlannerStore(join(root,'planner.sqlite'));
 assert.deepEqual(current.db.prepare("SELECT sql FROM sqlite_schema WHERE type='table' ORDER BY name").all(),before);
 assert.deepEqual(current.db.prepare('SELECT * FROM workflow_runs ORDER BY official_request_id').all(),history);
 const local=await call('request_submit',{sourceId:'local',requestType:'audio.generate',idempotencyKey:'rollback-local',payload:generated});
 assert.equal((await legacy.pollOnce(current,rs)).acknowledged,1);
 assert.equal(current.db.prepare('SELECT state FROM workflow_runs WHERE official_request_id=?').get(local.officialRequestId).state,'planned');
});

test('Space intake shares typed NPC/Concept validators and Planner claim shape',async t=>{
 const npc={schemaVersion:'ngs.npc-one-stop.v1',projectId:'binari',intent:'Fixture request',character:{
  npcId:'npc.fixture',name:'Fixture',slug:'fixture',role:'Guide',actId:'story.main.act01',chapterId:'chapter01'},
  sourceRefs:['fixture'],includeCombat:false};
 const concept={schemaVersion:'ngs.concept-category-workflow.v1',projectId:'binari',operation:'add',
  categoryId:'category.add.'+'a'.repeat(32),groupId:'world',title:'Fixture',outline:'Outline',requirements:'Requirements',
  requiredSections:['Outline']};
 const planningAdd={schemaVersion:'ngs.npc-planning-add.v1',projectId:'binari',npcId:npc.character.npcId,
  categoryId:'category.add.'+'b'.repeat(32),title:'Fixture',outline:'Outline',requirements:'Requirements',requiredSections:['Outline']};
 const planningUpdate={schemaVersion:'ngs.npc-planning-update.v1',projectId:'binari',npcId:npc.character.npcId,
  role:'overview',requirements:'Requirements'};
 const valid=[['npc.workflow',npc],['concept.category.workflow',concept],['npc.planning.add',planningAdd],['npc.planning.update',planningUpdate]];
 const rows=new Map(valid.map(([type,payload],i)=>['typed-'+i,spaceQueue('typed-'+i,type,payload)]));
 rows.set('foreign',spaceQueue('foreign','npc.planning.update',{...planningUpdate,projectId:'foreign'}));
 rows.set('key-mismatch',{...spaceQueue('key-mismatch','npc.planning.update',planningUpdate),idempotencyKey:'alternate'});
 const {requiredSections,...legacyAdd}=concept;
 rows.set('legacy',spaceQueue('legacy','concept.category.workflow',legacyAdd));
 const {call}=await fixture(t,async queueId=>rows.get(queueId));
 for(const [i,[type]] of valid.entries()){
  const queueId='typed-'+i;
  const registration=await call(toolSubmit,{queueId});
  assert.equal(registration.status,'registered',type);
  assert.equal(registration.officialRequestId,'request.official.'+sha(canonical(['request.official.v1','space','binari',queueId])));
 }
 for(const [queueId,status] of [['foreign',400],['key-mismatch',403],['legacy',400]])
  assert.equal((await call(toolSubmit,{queueId})).status,status);
 const {buildPlan}=await import('../workflow-planner/planner.mjs');
 const {requests}=await call('request_workflow_list',{});
 assert.equal(requests.length,4);
 for(const request of requests){
  assert.equal(request.sourceId,'space');
  assert.ok(buildPlan(request).tasks.length>0);
 }
});

test('local official registration and Planner preserve automatic 60-second target only when requested',async t=>{
 const {call}=await fixture(t);
 const submit=(id,payload)=>call('request_submit',{sourceId:'local',requestType:'audio.generate',idempotencyKey:id,payload});
 const exact={...generated,targetDurationSeconds:60};
 assert.equal((await submit('opt-in',exact)).status,'registered');
 assert.equal((await submit('legacy',generated)).status,'registered');
 for(const [i,change] of [{targetDurationSeconds:59},{targetDurationSeconds:null},{durationSeconds:10}].entries())
  assert.equal((await submit('bad-'+i,{...exact,...change})).status,400);
 const {requests}=await call('request_workflow_list',{});
 const {buildPlan}=await import('../workflow-planner/planner.mjs');
 for(const row of requests)assert.equal(buildPlan(row).tasks[0].type,'audio.generate');
 assert.deepEqual(requests.map(r=>r.payload.targetDurationSeconds).sort(),[60,undefined]);
});

test('direct concept and vocal modes carry canonical private inputs through MCP and Planner',async t=>{
 const {call}=await fixture(t);
 const {buildPlan}=await import('../workflow-planner/planner.mjs');
 const submit=(id,payload)=>call('request_submit',{sourceId:'local',requestType:'audio.generate',idempotencyKey:id,payload});
 const conceptText='직접 적은 컨셉\n청음용 🎵';
 const lyricsRequest='달의 표정을 그릴 짧은 가사\n반복하지 않기';
 const verbatim='  [Verse]\r\n별이 떴어\n  ';
 const direct={...generated,conceptId:'direct',conceptText};
 const requested={...direct,lyricsRequest,targetDurationSeconds:60};
 const exact={...direct,text:verbatim};
 assert.equal((await submit('direct-generated',{...requested,conceptText:'  '+conceptText+'\u00a0',lyricsRequest:'\n'+lyricsRequest+' '})).status,'registered');
 assert.equal((await submit('direct-verbatim',exact)).status,'registered');
 assert.equal((await submit('master-generated',{...generated,lyricsRequest})).status,'registered');
 assert.equal((await submit('direct-generated',requested)).status,'registered');
 assert.equal((await submit('direct-verbatim',{...exact,text:verbatim.trim()})).status,409);
 const {requests}=await call('request_workflow_list',{});
 assert.equal(requests.length,3);
 for(const row of requests){
  const plan=buildPlan(row);
  assert.equal(plan.payload,canonical(row.payload));
  assert.equal(row.payloadSha256,sha(plan.payload));
  assert.equal(plan.tasks.length,1);
  assert.deepEqual(plan.tasks[0].input,{kind:'bgm',conceptId:row.payload.conceptId});
 }
 for(const payload of [requested,exact,{...generated,lyricsRequest}])
  assert.ok(requests.some(row=>canonical(row.payload)===canonical(payload)));
 for(const bad of [
  {...generated,conceptId:'direct'},
  {...generated,conceptText},
  {...direct,conceptText:' \n '},
  {...direct,conceptText:'🎵'.repeat(4001)},
  {...direct,conceptText:'forbidden\u0000control'},
  {...direct,conceptText:'\u000bforbidden control'},
  {...direct,lyricsRequest:''},
  {...direct,lyricsRequest:'🎵'.repeat(4001)},
  {...direct,lyricsRequest:'forbidden\u0000control'},
  {...exact,lyricsRequest},
  {...requested,durationSeconds:30},
  {...exact,durationSeconds:30},
  {...direct,text:' \n '},
  {...direct,unexpectedField:true},
 ]){
  assert.equal((await submit('invalid',bad)).status,400);
 }
});

test('2D motion conversion uses verified Space provenance and the same active destination conflict as Local',async t=>{
 const type='motion-preset.convert-2d';
 const payload={schemaVersion:'ngs.motion-preset-convert-2d.v1',projectId:'binari',
  sourcePresetId:'Walk.3D',variantPresetId:'walk-sprite-2d',label:'걷기 2D'};
 const snapshots=new Map([['site-motion',spaceQueue('site-motion',type,payload)]]);
 const {call,root}=await fixture(t,async id=>snapshots.get(id));
 const official=await call(toolSubmit,{queueId:'site-motion'});
 assert.equal(official.status,'registered');
 const [{sourceId,sourceKey,officialRequestId,...feed}]=(await call('request_workflow_list',{})).requests;
 assert.equal(sourceId,'space');assert.equal(sourceKey,'site-motion');assert.equal(officialRequestId,official.officialRequestId);
 const modules=process.env.WORKFLOW_PLANNER_SITE_SOURCE??fileURLToPath(new URL('../workflow-planner',import.meta.url));
 const {PlannerStore}=await import(pathToFileURL(join(modules,'planner.mjs')).href);
 const store=new PlannerStore(join(root,'planner.sqlite'));t.after(()=>store.close());
 assert.throws(()=>store.stage({...feed,sourceId,sourceKey:'spoofed',officialRequestId}),/WORKFLOW_ROW_INVALID/);
 assert.equal(store.stage({...feed,sourceId,sourceKey,officialRequestId}),true);
 assert.equal(store.db.prepare('SELECT count(*) AS n FROM workflow_tasks').get().n,10);
 assert.equal((await call('request_submit',{sourceId:'local',requestType:type,idempotencyKey:'local-motion',payload})).status,409);
 assert.equal((await call('request_cancel',{officialRequestId})).status,404);
 assert.equal((await call('request_update_status',{officialRequestId,status:'planned'})).status,'planned');
 assert.equal((await call(toolSubmit,{queueId:'site-motion'})).status,'planned');
});
