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
async function fixture(t,readSpaceRequest,options={}) {
 const root=mkdtempSync(join(realpathSync(tmpdir()),'rs-site-fixture-'));
 const server=createRequestService({directory:join(root,'requests'),readSpaceRequest,...options});
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
 assert.equal(store.db.prepare('SELECT count(*) AS n FROM workflow_tasks').get().n,18);
 assert.equal((await call('request_submit',{sourceId:'local',requestType:type,idempotencyKey:'local-motion',payload})).status,409);
 assert.equal((await call('request_cancel',{officialRequestId})).status,404);
 assert.equal((await call('request_update_status',{officialRequestId,status:'planned'})).status,'planned');
 assert.equal((await call(toolSubmit,{queueId:'site-motion'})).status,'planned');
});

test('formal Local and verified Space separation require a completed BGM and never reserve or mutate the original',async t=>{
 const snapshots=new Map();
 const {call}=await fixture(t,async id=>snapshots.get(id));
 const local=(requestType,idempotencyKey,payload)=>call('request_submit',{sourceId:'local',requestType,idempotencyKey,payload});
 const source=await local('audio.generate','source-bgm',generated);
 const body={schemaVersion:'ngs.audio-separate.v1',projectId:'binari',sourceOfficialRequestId:source.officialRequestId,sourceSha256:'a'.repeat(64)};
 const deny={error:'OFFICIAL_AUDIO_SEPARATE_SOURCE_NOT_COMPLETED',status:404};
 assert.deepEqual(await local('audio.separate','pre-completed',body),deny);
 snapshots.set('site-audio-stems',spaceQueue('site-audio-stems','audio.separate',body));
 assert.deepEqual(await call(toolSubmit,{queueId:'site-audio-stems'}),deny);
 assert.deepEqual(await local('audio.separate','invalid',{...body,sourceSha256:'f'.repeat(63)+'\n'}),
  {error:'NGS_AUDIO_SEPARATE_PAYLOAD_INVALID',status:400});
 for(const status of ['planned','completed'])
  assert.equal((await call('request_update_status',{officialRequestId:source.officialRequestId,status})).status,status);
 const original=(await call('request_list',{status:'completed'})).requests[0];
 const first=await local('audio.separate','local-stems',body);
 assert.equal(first.status,'registered');
 assert.deepEqual(await local('audio.separate','local-stems',body),first);
 assert.deepEqual(await local('audio.separate','local-stems',{...body,sourceSha256:'b'.repeat(64)}),
  {error:'OFFICIAL_REQUEST_IDEMPOTENCY_CONFLICT',status:409});
 const second=await local('audio.separate','another-local-stems',body);
 const third=await call(toolSubmit,{queueId:'site-audio-stems'});
 assert.equal(second.status,'registered');assert.equal(third.status,'registered');
 assert.equal(new Set([source,first,second,third].map(r=>r.officialRequestId)).size,4);
 assert.deepEqual((await call('request_list',{status:'completed'})).requests,[original]);
 const entries=(await call('request_list',{status:'registered'})).requests;
 assert(entries.every(r=>r.targetName==='음원 분리'&&r.targetNpcId===source.officialRequestId));
 const feed=(await call('request_workflow_list',{})).requests;
 assert(feed.every(r=>r.payloadSha256===feed[0].payloadSha256));
 assert.deepEqual(feed.find(r=>r.officialRequestId===third.officialRequestId).payload,body);
 assert.equal(feed.find(r=>r.officialRequestId===third.officialRequestId).sourceKey,'site-audio-stems');
});

test('owner-refetched Space map and assembly tickets use exact Local contracts and protected delete readiness',async t=>{
 const mapId='request.official.'+'a'.repeat(64),assemblyId='request.official.'+'b'.repeat(64);
 const mapRoot='canonical-files/binari/cinematics/maps',assemblyRoot='canonical-files/binari/cinematics/assemblies';
 const pins={revision:2,officialRequestId:'request.official.'+'c'.repeat(64)};
 const region=(id,h)=>({id,name:id,description:'Fixture region',backgroundId:'background.'+h.repeat(64),imageSha256:h.repeat(64)});
 const graph={title:'굿터',startRegionId:'yard',regions:[region('yard','a'),region('gate','b')],
  connections:[{id:'trail',from:'yard',to:'gate',hit:null,spawn:null,label:'길'}]};
 const bases={projectId:'binari'};
 const type='binari.ritual-well-water-demo';
 const mapGeneration={...bases,schemaVersion:'ngs.map-generate.v2',graph,intent:'동선 밑그림',
  design:{startNotes:'',portalNotes:'사이 문',collisionNotes:'',collisionMode:'none'}};
 const mapUpdate={...bases,schemaVersion:'ngs.map-update.v1',mapId,expectedRevision:1,
  graph:{...graph,connections:[{...graph.connections[0],hit:{x:2,y:3,width:20,height:30},spawn:{x:10,y:20}}]}};
 const assemblyGeneration={...bases,schemaVersion:'ngs.assembly-generate.v2',title:'우물',mapId,intent:'굿터',puzzles:[{id:'well-run',type,intent:''}]};
 const assemblyV1={...bases,schemaVersion:'ngs.assembly-generate.v1',title:'우물',mapId,intent:'굿터',puzzleType:type};
 const assemblyUpdate={...bases,schemaVersion:'ngs.assembly-update.v1',assemblyId,expectedRevision:2,puzzles:[]};
 const deletion=(root,path,expected=pins)=>({...bases,schemaVersion:'ngs.file-delete.v1',target:{root,path,kind:'directory'},expected});
 const mapDelete=deletion(mapRoot,mapId),assemblyDelete=deletion(assemblyRoot,assemblyId);
 const npcDelete=deletion('canonical-files/binari/npcs','character.fixture',{creationTaskId:'workflow.task.'+'d'.repeat(64)});
 const propDelete=deletion('canonical-files/binari/props','asset',{revision:1,sha256:'a'.repeat(64)});
 const backgroundDelete={...deletion('canonical-files/binari/cinematics/backgrounds','b'.repeat(64)+'.png',{sourceRequestId:mapId})};
 backgroundDelete.target.kind='file';
 const records=[['map.generate',mapGeneration],['map.update',mapUpdate],['assembly.generate',assemblyGeneration],
  ['assembly.update',assemblyUpdate],['assembly.generate',assemblyV1],['file.delete',mapDelete],['file.delete',assemblyDelete],['file.delete',npcDelete],['file.delete',propDelete],['file.delete',backgroundDelete]];
 const snapshots=new Map(records.map(([type,payload],i)=>['space-parity-'+i,spaceQueue('space-parity-'+i,type,payload)]));
 let ready=false,reads=0,probed=[];
 const {call}=await fixture(t,async id=>{reads++;return snapshots.get(id);},{fileDeleteReadiness:async root=>{
  probed.push(root);return ready; // intentionally serve true even for other roots; the hosted gate must still deny them.
 }});
 const submit=queueId=>call(toolSubmit,{queueId});
 assert.equal((await call('request_submit',{sourceId:'space',requestType:'map.generate',idempotencyKey:'spoof',payload:mapGeneration})).status,400);
 assert.equal((await call(toolSubmit,{queueId:'space-parity-0',payload:mapGeneration})).status,400);
 assert.equal(reads,0);
 const first=await submit('space-parity-0');
 assert.equal(first.status,'registered');
 assert.deepEqual(await submit('space-parity-0'),first);
 assert.deepEqual(await submit('space-parity-5'),{error:'NGS_FILE_DELETE_UNAVAILABLE',status:503});
 assert.deepEqual(await submit('space-parity-6'),{error:'NGS_FILE_DELETE_UNAVAILABLE',status:503});
 assert.deepEqual(probed,[mapRoot,assemblyRoot]);ready=true;
 for(let i=1;i<records.length;i++)assert.equal((await submit('space-parity-'+i)).status,'registered',records[i][0]);

 for(const [id,t,p] of [
  ['bad-project','map.generate',{...mapGeneration,projectId:'other'}],
  ['bad-map','map.update',{...mapUpdate,expectedRevision:0}],
  ['bad-assembly','assembly.generate',{...assemblyGeneration,puzzles:[]}],
  ['bad-delete','file.delete',deletion(assemblyRoot,'../'+assemblyId)],
  ['no-pin','file.delete',deletion(mapRoot,mapId,{revision:1})],
  ['stale-shape','file.delete',deletion('canonical-files/binari/npcs','character.fixture',{creationTaskId:'missing'})],
  ['cinematic-delete','file.delete',deletion('canonical-files/binari/cinematics','sequences/'+assemblyId,{sourceSha256:'a'.repeat(64)})],
 ]) {
  snapshots.set(id,spaceQueue(id,t,p));assert.equal((await submit(id)).status,400,id);
 }
 snapshots.set('borrowed',{...spaceQueue('borrowed','map.generate',mapGeneration),idempotencyKey:'space-parity-0'});
 assert.equal((await submit('borrowed')).status,403);
 snapshots.set('space-parity-0',spaceQueue('space-parity-0','map.generate',{...mapGeneration,intent:'다른 입력'}));
 assert.equal((await submit('space-parity-0')).status,409);
 const {requests}=await call('request_workflow_list',{});
 const {buildPlan}=await import('../workflow-planner/planner.mjs');
 assert.equal(requests.length,records.length);
 for(const row of requests){
  assert.equal(row.sourceId,'space');
  assert.equal(row.officialRequestId,'request.official.'+sha(canonical(['request.official.v1','space','binari',row.sourceKey])));
  const plan=buildPlan(row,{multiDependencies:true});
  assert.equal(plan.tasks[0].workerType,row.requestType==='file.delete'?'file':'document');
 }
 // The preexisting explicit Local typed prop delete remains available.
 assert.equal((await call('request_submit',{sourceId:'local',requestType:'file.delete',idempotencyKey:'local-prop-delete',
  payload:propDelete})).status,'registered');
});
