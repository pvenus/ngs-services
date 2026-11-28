import assert from 'node:assert/strict';
import {mkdtempSync,realpathSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import test from 'node:test';
import {DatabaseSync} from 'node:sqlite';
import {createRequestService} from '../request-service/request-service.mjs';
import {makeLoopbackTransport} from '../workflow-planner/rs.mjs';
import {canonical,sha} from '../workflow-planner/envelope.mjs';
import {PlannerStore,pollOnce,buildPlan} from '../workflow-planner/planner.mjs';
import {propKeys,propRef} from '../workflow-planner/prop-generate-plan.mjs';
import {preparePropPublication,verifyPropPublicationProof} from '../workflow-planner/prop-package-proof.mjs';

const p={schemaVersion:'ngs.prop-generate.v1',projectId:'binari',propId:'lantern',intent:'손잡이가 달린 등롱 프랍 한 종류',
 designContextSha256:'a'.repeat(64)};
function root(t){
 const path=mkdtempSync(join(realpathSync(tmpdir()),'prop-composite-'));
 t.after(()=>rmSync(path,{force:true,recursive:true}));return path;
}
async function server(t,temp,options={}){
 const s=createRequestService({directory:join(temp,'official'),...options});
 await new Promise(resolve=>s.listen(0,'127.0.0.1',resolve));
 t.after(()=>new Promise(resolve=>s.close(resolve)));
 const transport=makeLoopbackTransport(s.address().port),rpc=async(method,params)=>
  (await transport({jsonrpc:'2.0',id:1,method,params})).result;
 return {rpc,async call(name,args){
  const r=await rpc('tools/call',{name,arguments:args});
  if(r.isError)throw Object.assign(Error(r.structuredContent.error),{rsStatus:r.structuredContent.status});
  return r.structuredContent;
 }};
}
const command=(payload=p)=>({sourceId:'local',requestType:'prop.generate',idempotencyKey:'one-prop',payload});
const makeRow=payload=>({officialRequestId:'request.official.'+'1'.repeat(64),projectId:'binari',
 requestType:'prop.generate',schemaVersion:payload.schemaVersion,payload,payloadSha256:sha(canonical(payload)),status:'registered',registeredAt:1});

test('default RS genuinely declines and Planner cannot activate without a proof reader',async t=>{
 const temp=root(t),rs=await server(t,temp);
 const advertised=(await rs.rpc('tools/list',{})).tools.find(tool=>tool.name==='request_submit');
 assert(!advertised.inputSchema.properties.requestType.enum.includes('prop.generate'));
 await assert.rejects(rs.call('request_submit',command()),/PROP_GENERATE_RUNTIME_UNAVAILABLE/);
 assert.deepEqual((await rs.call('request_list',{})).requests,[]);
 assert.throws(()=>new PlannerStore(':memory:',{propGenerateEnabled:true}),/PROP_GENERATE_RUNTIME_UNAVAILABLE/);
 const store=new PlannerStore(':memory:');t.after(()=>store.close());
 assert.throws(()=>store.stage(makeRow(p)),/PROP_GENERATE_RUNTIME_UNAVAILABLE/);
});
test('same authority contract works for Space or Local provenance, motions disabled independently, no tech knobs',async t=>{
 const temp=root(t),queueId='ngs.space.request.fixture';
 const rs=await server(t,temp,{propGenerateEnabled:true,readSpaceRequest:async id=>{
  assert.equal(id,queueId);return {queueId,idempotencyKey:queueId,requestType:'prop.generate',payload:p};
 }});
 await assert.rejects(rs.call('request_submit',command({...p,motion:'subtle'})),/PROP_GENERATE_MOTION_UNAVAILABLE/);
 await assert.rejects(rs.call('request_submit',command({...p,states:[]})),/NGS_PROP_GENERATE_PAYLOAD_INVALID/);
 await assert.rejects(rs.call('request_submit',{...command(),sourceId:'space'}),/REQUEST_SUBMIT_INVALID/);
 const site=await rs.call('request_submit_space',{queueId});
 const handoff=(await rs.call('request_workflow_list',{})).requests[0];
 assert.equal(handoff.officialRequestId,site.officialRequestId);
 assert.equal(handoff.sourceId,'space');
 assert.equal(handoff.sourceKey,queueId);
 assert.equal(buildPlan(handoff).tasks.filter(x=>x.workerType==='image').length,1);
});

test('formal one request: still and subtle exact topology, one base and exact source lineage',()=>{
 const still=buildPlan(makeRow(p)).tasks, motion=buildPlan(makeRow({...p,motion:'subtle'})).tasks;
 assert.equal(still.length,5);assert.equal(motion.length,8);
 for(const [tasks,animated] of [[still,false],[motion,true]]){
  const by=Object.fromEntries(tasks.map(x=>[x.key,x]));
  assert.equal(tasks.filter(x=>x.workerType==='image').length,1);
  const base=by[propKeys.base],detail=by[propKeys.detail],pixel=by[propKeys.pixel],final=by[propKeys.publish];
  assert.equal(base.dependsOn,by[propKeys.design].id);
  assert.equal(detail.input.baseTaskId,base.id);assert.equal(pixel.dependsOn,detail.id);
  assert.equal(final.dependsOn,animated?by[propKeys.animPixel].id:pixel.id);
  assert.deepEqual(final.input.dependsOnTaskIds,tasks.slice(0,-1).map(x=>x.id));
  if(animated){assert.equal(by[propKeys.motion].dependsOn,base.id);assert.equal(by[propKeys.animDetail].dependsOn,by[propKeys.motion].id);}
  else assert.equal(detail.dependsOn,base.id);
  assert(!tasks.some(x=>x.type==='prop.image-generate'||x.type==='animation.i2v'));
 }
});

for(const profile of [null,'spirit-v1','spirit-v2'])
test('real MCP ingress/Planner: one pack only after same-run complete proof and independent Filex receipt readback (profile='+profile+')',async t=>{
 const temp=root(t),rs=await server(t,temp,{propGenerateEnabled:true,propGenerateMotionEnabled:true});
 const payload={...p,motion:'subtle',...(profile?{presentationProfile:profile}:{})};
 const small=profile?102:128;
 if(profile)await assert.rejects(rs.call('request_submit',command({...payload,presentationProfile:'unknown'})),/NGS_PROP_GENERATE_PAYLOAD_INVALID/);
 const {officialRequestId}=await rs.call('request_submit',command(payload));
 let proof=null,reads=0;
 const store=new PlannerStore(join(temp,'planner.sqlite'),{propGenerateEnabled:true,propGenerateMotionEnabled:true,
  readPropPublication:async()=>{reads++;return proof;}});
 t.after(()=>store.close());
 assert.equal((await pollOnce(store,rs)).acknowledged,1);
 assert.equal((await pollOnce(store,rs)).staged,0);
 const row={...makeRow(payload),officialRequestId},run={...row,state:'planned',reason:null},tasks=buildPlan(row).tasks,final=tasks.at(-1);
 if(profile){
  const by=Object.fromEntries(tasks.map(t=>[t.key,t]));
  assert.equal(by[propKeys.spirit].type,'prop.generate.spirit');
  assert.equal(by[propKeys.spirit].dependsOn,by[propKeys.detail].id);
  assert.deepEqual(final.additionalDependencies,[by[propKeys.pixel].id,by[propKeys.spirit].id]);
  assert.ok(tasks.every(t=>t.input.presentationProfile===profile));
  if(profile==='spirit-v2'){
   assert.deepEqual([by[propKeys.pixelSource].dependsOn,by[propKeys.pixelDetail].dependsOn,by[propKeys.pixel].dependsOn],
    [by[propKeys.detail].id,by[propKeys.pixelSource].id,by[propKeys.pixelDetail].id]);
   assert.equal(by[propKeys.pixel].input.geometryTaskId,by[propKeys.detail].id);
   assert.equal(tasks.length,11);
  }
 }
 const rows=()=>store.db.prepare('SELECT * FROM workflow_tasks WHERE official_request_id=?').all(officialRequestId);
 const evidence=Object.fromEntries(tasks.slice(0,-1).map((x,i)=>[x.key,(i+1).toString(16).repeat(64)]));
 const put=store.db.prepare('UPDATE workflow_tasks SET status=?,result_ref=?,updated_at=9 WHERE task_id=?');
 const claim={officialRequestId,projectId:p.projectId,taskId:final.id,payloadSha256:run.payloadSha256,claimStamp:9};
 const media=(key,width,height,animated=false)=>({assetId:evidence[key]+'.png',sha256:evidence[key],bytes:600,width,height,
  mediaType:animated?'image/apng':'image/png',frameDurationsMs:animated?[100,100]:[]});
 const manifest={schemaVersion:'ngs.prop-package.v1',officialRequestId,projectId:'binari',propId:p.propId,
  requestSha256:run.payloadSha256,designContextSha256:p.designContextSha256,revision:1,title:'등롱',parentSha256:null,
  media:{detail:media(propKeys.detail,512,512),pixel:media(propKeys.pixel,small,small),...(profile?{spirit:media(propKeys.spirit,small,small)}:{})},
  animations:[{id:'initial',title:'미세 움직임',officialRequestId,requestSha256:run.payloadSha256,media:{
   detail:media(propKeys.animDetail,512,512,true),pixel:media(propKeys.animPixel,small,small,true)}}],
  interaction:{pixelOpen:{action:'open',x:1,y:2,width:15,height:15},detailTrigger:{action:'activate',x:2,y:3,width:80,height:80}}};
 put.run('running',null,final.id);
 for(const task of tasks.slice(0,-2))put.run('completed',propRef(task.key,evidence[task.key]),task.id);
 assert.throws(()=>preparePropPublication(run,rows(),claim,manifest,{head:null}),/PROP_PACKAGE_PROOF_INVALID/);
 assert.equal((await pollOnce(store,rs)).completed,0);assert.equal(reads,0);
 const pixel=tasks.at(-2);put.run('completed',propRef(pixel.key,evidence[pixel.key]),pixel.id);
 assert.throws(()=>preparePropPublication(run,rows(),{...claim,claimStamp:8},manifest,{head:null}),/PROP_PACKAGE_PROOF_INVALID/);
 const wrongMedia={...manifest,animations:manifest.animations.map(clip=>({...clip,media:{...clip.media,pixel:{...clip.media.pixel,frameDurationsMs:[80,120]}}}))};
 assert.throws(()=>preparePropPublication(run,rows(),claim,wrongMedia,{head:null}),/PROP_PACKAGE_PROOF_INVALID/);
 if(profile){
  const wrong={...manifest,media:{...manifest.media,spirit:media(propKeys.pixel,small,small)}};
  assert.throws(()=>preparePropPublication(run,rows(),claim,wrong,{head:null}),/PROP_PACKAGE_PROOF_INVALID/);
 }
 const candidate=preparePropPublication(run,rows(),claim,manifest,{head:null});
 put.run('completed',candidate.resultRef,final.id);
 assert.equal((await pollOnce(store,rs)).completed,0);assert.equal(reads,1);
 const receipt={schemaVersion:'ngs.prop-package-publication-receipt.v1',authority:'filex',status:'committed',...candidate};
 const bad={...receipt,members:[...candidate.members].reverse()};
 proof={manifest,receipt:bad};
 assert.equal((await pollOnce(store,rs)).completed,0);
 assert.equal((await rs.call('request_list',{})).requests[0].status,'planned');
 proof={manifest,receipt};
 assert.equal(verifyPropPublicationProof(candidate,proof),true);
 assert.equal((await pollOnce(store,rs)).completed,1);
 assert.equal((await rs.call('request_list',{})).requests[0].status,'completed');
 assert.equal((await pollOnce(store,rs)).completed,0);
});

test('exhausted root design failure settles blocked descendants without declaring their skipped work complete',async t=>{
 const temp=root(t),rs=await server(t,temp,{propGenerateEnabled:true});
 const {officialRequestId}=await rs.call('request_submit',command());
 const store=new PlannerStore(':memory:',{propGenerateEnabled:true,readPropPublication:async()=>null});t.after(()=>store.close());
 assert.equal((await pollOnce(store,rs)).acknowledged,1);
 store.db.prepare("UPDATE workflow_tasks SET status='failed',retry_count=retry_limit WHERE task_key=? AND official_request_id=?").run(propKeys.design,officialRequestId);
 assert.equal((await pollOnce(store,rs)).failed,1);
 assert.equal((await rs.call('request_list',{})).requests[0].status,'failed');
 assert.equal(store.db.prepare("SELECT count(*) AS n FROM workflow_tasks WHERE status='pending'").get().n,4);
});

const revisionCase=(requestType,payload,suffix)=>({officialRequestId:'request.official.'+suffix.repeat(64),
 projectId:'binari',requestType,schemaVersion:payload.schemaVersion,payload,payloadSha256:sha(canonical(payload)),
 status:'registered',registeredAt:1});
const still=(digest,width,height)=>({assetId:digest+'.png',sha256:digest,bytes:600,width,height,mediaType:'image/png',frameDurationsMs:[]});
const animate=(digest,width,height)=>({...still(digest,width,height),mediaType:'image/apng',frameDurationsMs:[100,100]});
const basePack={schemaVersion:'ngs.prop-package.v1',officialRequestId:'request.official.'+'a'.repeat(64),projectId:'binari',propId:'lantern',
 revision:1,parentSha256:null,requestSha256:'1'.repeat(64),designContextSha256:'2'.repeat(64),title:'기존 등롱',
 media:{detail:still('3'.repeat(64),512,512),pixel:still('4'.repeat(64),128,128)},interaction:{pixelOpen:null,detailTrigger:null},
 animations:[{id:'old-flutter',title:'기존 움직임',officialRequestId:'request.official.'+'b'.repeat(64),
  requestSha256:'7'.repeat(64),media:{detail:animate('6'.repeat(64),512,512),pixel:animate('8'.repeat(64),128,128)}}]};
const sourcePin={revision:basePack.revision,sha256:sha(canonical(basePack))};
const editPayload={schemaVersion:'ngs.prop-regenerate.v1',projectId:'binari',propId:'lantern',source:sourcePin,
 intent:'이전 디자인을 대체하는 새 등롱',designContextSha256:'c'.repeat(64)};
const animPayload={schemaVersion:'ngs.prop-animation-generate.v1',projectId:'binari',propId:'lantern',source:sourcePin,
 motionId:'gentle-sway',prompt:'기존 등롱이 가볍게 흔들립니다'};
function fixtureRows(row,values={}){
 const graph=buildPlan(row).tasks,last=graph.at(-1),run={...row,state:'planned',reason:null};
 const rows=graph.map((task,i)=>({official_request_id:run.officialRequestId,task_id:task.id,task_key:task.key,
  task_type:task.type,worker_type:task.workerType,depends_on_task_id:task.dependsOn,task_input:canonical(task.input),
  status:task===last?'running':'completed',result_ref:task===last?null:propRef(task.key,values[task.key]??(i+1).toString(16).repeat(64)),updated_at:7}));
 return {run,rows,claim:{officialRequestId:run.officialRequestId,projectId:'binari',taskId:last.id,payloadSha256:row.payloadSha256,claimStamp:7}};
}
test('full regenerate is retired at Local/Space intake; registered backlog settles without new tasks',async t=>{
 const temp=root(t),queueId='ngs.space.request.retired';
 const rs=await server(t,temp,{propGenerateEnabled:true,propMutationsEnabled:true,readSpaceRequest:async()=>({
  queueId,idempotencyKey:queueId,requestType:'prop.regenerate',payload:editPayload
 })});
 const submit=(requestType,payload,key)=>rs.call('request_submit',{sourceId:'local',requestType,payload,idempotencyKey:key});
 await assert.rejects(submit('prop.animation.generate',animPayload,'motion-off'),/PROP_GENERATE_MOTION_UNAVAILABLE/);
 const retired={message:'PROP_REGENERATE_RETIRED',rsStatus:422};
 await assert.rejects(submit('prop.regenerate',editPayload,'regenerate'),retired);
 await assert.rejects(rs.call('request_submit_space',{queueId}),retired);
 const schema=(await rs.rpc('tools/list',{})).tools.find(x=>x.name==='request_submit').inputSchema;
 assert(!schema.properties.requestType.enum.includes('prop.regenerate'));
 assert(schema.properties.requestType.enum.includes('prop.scale.generate'));
 assert.deepEqual((await rs.call('request_list',{})).requests,[]);
 const db=new DatabaseSync(join(temp,'official','official-requests.sqlite'));t.after(()=>db.close());
 const newRow=revisionCase('prop.regenerate',editPayload,'f'),existing=revisionCase('prop.regenerate',editPayload,'e');
 for(const legacy of [newRow,existing])
  db.prepare(`INSERT INTO official_requests(official_request_id,source_id,project_id,idempotency_key,request_type,
   schema_version,payload_json,payload_sha256,state,registered_at) VALUES(?,'local','binari',?,?,?, ?,?,'registered',?)`)
   .run(legacy.officialRequestId,legacy.officialRequestId,legacy.requestType,legacy.schemaVersion,canonical(legacy.payload),legacy.payloadSha256,legacy.registeredAt);
 const store=new PlannerStore(':memory:',{propGenerateEnabled:true,propMutationsEnabled:true,readPropPublication:async()=>null});
 t.after(()=>store.close());
 assert.throws(()=>store.stage(newRow),/PROP_REGENERATE_RETIRED/);
 // Existing pending graph is settled without claiming any retired task.
 store.db.prepare("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,?,'awaiting_rs',?,NULL)")
  .run(existing.officialRequestId,existing.projectId,existing.requestType,existing.schemaVersion,canonical(existing.payload),
   existing.payloadSha256,existing.registeredAt,8);
 const plan=buildPlan(existing);
 const add=store.db.prepare(`INSERT INTO workflow_tasks(task_id,official_request_id,task_key,task_type,worker_type,
  task_input,depends_on_task_id,updated_at) VALUES(?,?,?,?,?,?,?,8)`);
 for(const t of plan.tasks)add.run(t.id,existing.officialRequestId,t.key,t.type,t.workerType,canonical(t.input),t.dependsOn);
 const status=await pollOnce(store,rs);
 assert.equal(status.acknowledged,0);assert.equal(status.failed,2);assert.equal(status.pollError,false);
 const state=(await rs.call('request_list',{})).requests;
 assert.equal(state.find(x=>x.officialRequestId===newRow.officialRequestId).status,'failed');
 assert.equal(state.find(x=>x.officialRequestId===existing.officialRequestId).status,'failed');
 assert.equal(store.db.prepare('SELECT COUNT(*) AS n FROM workflow_tasks').get().n,plan.tasks.length);
});

test('immutable regeneration and append enforce the pinned current head; append cannot replace base or overwrite prior clips',()=>{
 const meta=fixtureRows(revisionCase('prop.regenerate',editPayload,'c'));
 const next=(scope,body)=>({...basePack,officialRequestId:scope.run.officialRequestId,requestSha256:scope.run.payloadSha256,
  revision:2,parentSha256:sourcePin.sha256,...body});
 const manifest=next(meta,{title:'새롭게 재생성된 등롱',designContextSha256:editPayload.designContextSha256,animations:[],
  media:{detail:still('3'.repeat(64),512,512),pixel:still('4'.repeat(64),128,128)}});
 assert(preparePropPublication(meta.run,meta.rows,meta.claim,manifest,{prior:basePack,head:sourcePin}));
 assert.throws(()=>preparePropPublication(meta.run,meta.rows,meta.claim,{...manifest,animations:basePack.animations},{prior:basePack,head:sourcePin}),/PROP_PACKAGE_PROOF_INVALID/);
 assert.throws(()=>preparePropPublication(meta.run,meta.rows,meta.claim,{...manifest,media:{...manifest.media,
  pixel:still('b'.repeat(64),128,128)}},{prior:basePack,head:sourcePin}),/PROP_PACKAGE_PROOF_INVALID/);
 const anim=fixtureRows(revisionCase('prop.animation.generate',animPayload,'d'),
  {[propKeys.animDetail]:'e'.repeat(64),[propKeys.animPixel]:'f'.repeat(64)});
 const clip={id:animPayload.motionId,title:'가벼운 흔들림',officialRequestId:anim.run.officialRequestId,requestSha256:anim.run.payloadSha256,
  media:{detail:animate('e'.repeat(64),512,512),pixel:animate('f'.repeat(64),128,128)}};
 const appended=next(anim,{animations:[...basePack.animations,clip]});
 const evidence=preparePropPublication(anim.run,anim.rows,anim.claim,appended,{prior:basePack,head:sourcePin});
 const receipt={schemaVersion:'ngs.prop-package-publication-receipt.v1',authority:'filex',status:'committed',...evidence};
 assert.equal(verifyPropPublicationProof(evidence,{receipt,manifest:appended,prior:basePack}),true);
 assert.equal(verifyPropPublicationProof(evidence,{receipt,manifest:appended,prior:{...basePack,title:'forged'}}),false);
 for(const failure of [{head:{revision:2,sha256:'a'.repeat(64)},manifest:appended},
  {head:sourcePin,manifest:{...appended,animations:[clip]}},
  {head:sourcePin,manifest:{...appended,animations:[...basePack.animations,{...clip,id:'old-flutter'}]}},
  {head:sourcePin,manifest:{...appended,title:'changed without editing'}}]){
  assert.throws(()=>preparePropPublication(anim.run,anim.rows,anim.claim,failure.manifest,{prior:basePack,head:failure.head}),
   /PROP_PACKAGE_PROOF_INVALID/);
 }
});

test('normal RS capability gate pauses current prop intake and refuses to admit motion',async t=>{
 const temp=root(t);let ready=false;
 const rs=await server(t,temp,{propGenerateEnabled:true,propMutationsEnabled:true,propPackageReadiness:async()=>ready});
 const schema=async()=>(await rs.rpc('tools/list',{})).tools.find(x=>x.name==='request_submit').inputSchema.properties.requestType.enum;
 assert(!(await schema()).includes('prop.generate'));
 await assert.rejects(rs.call('request_submit',command()),/PROP_GENERATE_RUNTIME_UNAVAILABLE/);
 ready=true;
 assert((await schema()).includes('prop.generate'));
 assert(!(await schema()).includes('prop.regenerate'));
 assert(!(await schema()).includes('prop.animation.generate'));
 await assert.rejects(rs.call('request_submit',command({...p,motion:'subtle'})),/PROP_GENERATE_MOTION_UNAVAILABLE/);
 const accepted=await rs.call('request_submit',command());
 assert.equal((await rs.call('request_workflow_list',{})).requests[0].officialRequestId,accepted.officialRequestId);
 ready=false;
 assert.deepEqual((await rs.call('request_workflow_list',{})).requests,[]);
 assert.equal((await rs.call('request_list',{})).requests[0].status,'registered');
 ready=true;
 assert.equal((await rs.call('request_workflow_list',{})).requests[0].officialRequestId,accepted.officialRequestId);
});

test('append advertised/submitted/fed only with renderer capability; initial still remains independent',async t=>{
 const {propPackageReady}=await import('../request-service/prop-package-readiness.mjs');
 const pds=modes=>async(url)=>{
  assert.equal(url,'http://127.0.0.1:8030/v1/props/ownership/workflow-package');
  return Response.json({enabled:true,schemaVersion:'workflow.prop-package-write.v1',modes});
 };
 let modes=['still'];
 const temp=root(t),rs=await server(t,temp,{propGenerateEnabled:true,propMutationsEnabled:true,propAnimationEnabled:true,
  propPackageReadiness:()=>propPackageReady(pds(modes)),
  propAnimationReadiness:()=>propPackageReady(pds(modes),'append-animation')});
 const schema=async()=>(await rs.rpc('tools/list',{})).tools.find(x=>x.name==='request_submit').inputSchema.properties.requestType.enum;
 const submit=()=>rs.call('request_submit',{sourceId:'local',idempotencyKey:'signed-motion',requestType:'prop.animation.generate',payload:animPayload});
 assert(!(await schema()).includes('prop.animation.generate'));
 await assert.rejects(submit(),/PROP_GENERATE_MOTION_UNAVAILABLE/);
 modes=['still','append-animation'];
 assert((await schema()).includes('prop.animation.generate'));
 const accepted=await submit();
 await assert.rejects(rs.call('request_submit',command({...p,motion:'subtle'})),/PROP_GENERATE_MOTION_UNAVAILABLE/);
 const staticResult=await rs.call('request_submit',command());
 const listed=async()=>(await rs.call('request_workflow_list',{})).requests.map(r=>r.officialRequestId);
 assert((await listed()).includes(accepted.officialRequestId));
 modes=['still'];
 assert.deepEqual(await listed(),[staticResult.officialRequestId]);
 const store=new PlannerStore(':memory:',{propGenerateEnabled:true,propMutationsEnabled:true,propAnimationEnabled:true,readPropPublication:async()=>null});
 t.after(()=>store.close());
 modes=['still','append-animation'];
 assert.equal((await pollOnce(store,rs)).acknowledged,2);
 const tasks=store.db.prepare('SELECT task_key,worker_type FROM workflow_tasks WHERE official_request_id=? ORDER BY rowid').all(accepted.officialRequestId);
 assert.deepEqual(tasks.map(t=>[t.task_key,t.worker_type]),[[propKeys.motion,'animation'],[propKeys.animDetail,'postprocess'],[propKeys.animPixel,'postprocess'],[propKeys.publish,'postprocess']]);
 for(const invalid of [['append-animation'],['still','subtle'],['still','append-animation','still']])
  assert.equal(await propPackageReady(pds(invalid)),false);
});

test('only the exact candle append profile is admitted, fully hash-bound, and keeps the same DAG',async t=>{
 const temp=root(t);let ready=false;
 const rs=await server(t,temp,{propGenerateEnabled:true,propMutationsEnabled:true,propAnimationEnabled:true,
  propAnimationReadiness:async()=>ready});
 const {candleChromaPropId,candleChromaProfile}=await import('../request-service/prop-generate-envelope.mjs');
 const type='prop.animation.generate';
 const legacy={...animPayload,propId:candleChromaPropId};
 const compatibility={...legacy,renderProfile:candleChromaProfile};
 const submit=(key,payload)=>rs.call('request_submit',{sourceId:'local',requestType:type,payload,idempotencyKey:key});
 await assert.rejects(submit('not-ready',compatibility),/PROP_GENERATE_MOTION_UNAVAILABLE/);
 ready=true;
 for(const value of [{...compatibility,propId:p.propId},{...compatibility,renderProfile:'other'},
  {...compatibility,renderProfile:null},{...legacy,greenKey:true}])
  await assert.rejects(submit('inexact',value),/NGS_PROP_GENERATE_PAYLOAD_INVALID/);
 const accepted=await submit('exact',compatibility);
 const feed=(await rs.call('request_workflow_list',{})).requests[0];
 assert.equal(feed.officialRequestId,accepted.officialRequestId);
 assert.deepEqual(feed.payload,compatibility);
 assert.equal(feed.payloadSha256,sha(canonical(compatibility)));
 assert.notEqual(feed.payloadSha256,sha(canonical(legacy)));
 const old={...feed,payload:legacy,payloadSha256:sha(canonical(legacy))};
 assert.deepEqual(buildPlan(old).tasks,buildPlan(feed).tasks);
 const check=new PlannerStore(':memory:',{propGenerateEnabled:true,propMutationsEnabled:true,
  propAnimationEnabled:true,readPropPublication:async()=>null});
 t.after(()=>check.close());
 assert.equal((await pollOnce(check,rs)).acknowledged,1);
 const stored=check.db.prepare('SELECT payload,payload_sha256 FROM workflow_runs').get();
 assert.equal(stored.payload,canonical(compatibility));
 assert.equal(stored.payload_sha256,feed.payloadSha256);
 await assert.rejects(submit('exact',legacy),/OFFICIAL_REQUEST_IDEMPOTENCY_CONFLICT/);
});

test('reference CREATE retains exact optional pin across official Local and Space intake and only to base stage',async t=>{
 const reference={propId:'source-lantern',revision:3,sha256:'b'.repeat(64)},payload={...p,reference};
 const temp=root(t),queueId='ngs.space.ref.fixture';
 const rs=await server(t,temp,{propGenerateEnabled:true,propMutationsEnabled:true,readSpaceRequest:async id=>{
  assert.equal(id,queueId);return {queueId,idempotencyKey:queueId,requestType:'prop.generate',payload};
 }});
 await rs.call('request_submit',command(payload));
 await rs.call('request_submit_space',{queueId});
 const rows=(await rs.call('request_workflow_list',{})).requests;
 assert.equal(rows.length,2);
 for(const row of rows){
  assert.deepEqual(row.payload.reference,reference);
  const tasks=buildPlan(row).tasks;
  assert.equal(tasks.length,5);
  assert.deepEqual(tasks.find(x=>x.key===propKeys.base).input.reference,reference);
  assert(tasks.filter(x=>x.key!==propKeys.base).every(x=>!('reference' in x.input)));
 }
 await assert.rejects(rs.call('request_submit',command({...payload,reference:{...reference,propId:p.propId}})),/NGS_PROP_GENERATE_PAYLOAD_INVALID/);
 await assert.rejects(rs.call('request_submit',command({...payload,reference:{...reference,url:'http://localhost/'}})),/NGS_PROP_GENERATE_PAYLOAD_INVALID/);
 await assert.rejects(rs.call('request_submit',{...command(),requestType:'prop.regenerate',payload:{...payload,
  schemaVersion:'ngs.prop-regenerate.v1',source:{revision:1,sha256:'c'.repeat(64)}}}),/PROP_REGENERATE_RETIRED/);
});

test('official scale intake plans independent postprocess tasks and one publish',async t=>{
 const temp=root(t),rs=await server(t,temp,{propGenerateEnabled:true,propMutationsEnabled:true});
 const advertised=(await rs.rpc('tools/list',{})).tools.find(tool=>tool.name==='request_submit');
 assert(advertised.inputSchema.properties.requestType.enum.includes('prop.scale.generate'));
 const source={revision:2,sha256:'c'.repeat(64)},base={schemaVersion:'ngs.prop-scale-generate.v1',projectId:'binari',propId:p.propId,source};
 const submit=(payload,key)=>rs.call('request_submit',{sourceId:'local',requestType:'prop.scale.generate',idempotencyKey:key,payload});
 for(const [index,scalePercents] of [[],[5,5],[15],[1],[0],[105],['10']].entries())
  await assert.rejects(submit({...base,scalePercents},'invalid-scale-'+index),/NGS_PROP_GENERATE_PAYLOAD_INVALID/);
 const {officialRequestId}=await submit({...base,scalePercents:[100,5,20]},'add-prop-scales');
 const {requests}=await rs.call('request_workflow_list',{});
 const row=requests.find(r=>r.officialRequestId===officialRequestId);
 const [first,second,third,pkg]=buildPlan(row).tasks;
 assert.deepEqual([first,second,third].map(t=>[t.key,t.type,t.workerType,t.input.scalePercent,t.dependsOn]),
  [[ 'prop_scale_005','prop.scale.derive','postprocess',5,null],
   ['prop_scale_020','prop.scale.derive','postprocess',20,first.id],
   ['prop_scale_100','prop.scale.derive','postprocess',100,second.id]]);
 assert.equal(pkg.type,'prop.scale.generate.replace-publish');
 assert.equal(pkg.dependsOn,third.id);
 assert.deepEqual(pkg.input,{propId:p.propId,source,scalePercents:[100,5,20],dependsOnTaskIds:[first.id,second.id,third.id]});
 assert.deepEqual(buildPlan(row,{splitPropScales:false}).tasks.map(t=>t.key),[propKeys.publish]);
 const generating={...p,scalePercents:[40,5],presentationProfile:'spirit-v2'};
 const tasks=buildPlan(makeRow(generating)).tasks;
 assert.deepEqual(tasks.at(-1).input.scalePercents,[40,5]);
 assert.equal(tasks.filter(t=>t.workerType==='image').length,2);
 const pair=tasks.filter(t=>t.type==='prop.scale.derive');
 assert.deepEqual(pair.map(t=>t.input.scalePercent),[5,40]);
 assert.equal(pair[0].dependsOn,tasks.find(t=>t.key===propKeys.pixelDetail).id);
 assert.equal(pair[1].dependsOn,pair[0].id);
 assert(tasks.at(-1).additionalDependencies.includes(pair[1].id));
 await assert.rejects(rs.call('request_submit',command({...p,scalePercents:[13]})),/NGS_PROP_GENERATE_PAYLOAD_INVALID/);
});

test('scale repair replaces only selected pairs and completion proof preserves all unselected media',()=>{
 const a='a'.repeat(64),b='b'.repeat(64),c='c'.repeat(64);
 const asset=(sha256,width,height=width)=>({assetId:sha256+'.png',sha256,width,height,bytes:100,mediaType:'image/png',frameDurationsMs:[]});
 const base={schemaVersion:'ngs.prop-package.v1',projectId:'binari',propId:'lantern',revision:1,officialRequestId:'request.official.'+b,
  requestSha256:a,designContextSha256:a,title:'등롱',parentSha256:null,media:{detail:asset(a,100),pixel:asset(b,20)},
  interaction:{pixelOpen:null,detailTrigger:null},animations:[],scaleVariants:[{scalePercent:5,pixel:asset(c,5),spirit:asset(b,5)},
   {scalePercent:20,pixel:asset(b,20),spirit:asset(c,20)}]};
 const source={revision:1,sha256:sha(canonical(base))},officialRequestId='request.official.'+a,requestType='prop.scale.generate';
 const payload={schemaVersion:'ngs.prop-scale-generate.v1',projectId:'binari',propId:'lantern',source,scalePercents:[5]};
 const run={...makeRow(payload),officialRequestId,requestType,state:'planned',reason:null};
 const [scale,task]=buildPlan({...makeRow(payload),officialRequestId,requestType}).tasks;
 const claim={officialRequestId,projectId:'binari',taskId:task.id,payloadSha256:run.payloadSha256,claimStamp:10};
 const pack={...base,officialRequestId,requestSha256:run.payloadSha256,revision:2,parentSha256:source.sha256,
  scaleVariants:[{scalePercent:5,pixel:asset(b,5),spirit:asset(c,5)},base.scaleVariants[1]]};
 const rows=[scale,task].map((t,i)=>({task_id:t.id,task_key:t.key,task_type:t.type,worker_type:t.workerType,
  depends_on_task_id:t.dependsOn,task_input:canonical(t.input),official_request_id:officialRequestId,status:i?'running':'completed',
  result_ref:i?null:'prop.scale-pair.'+sha(canonical(pack.scaleVariants[0])),updated_at:i?10:9}));
 const proof=preparePropPublication(run,rows,claim,pack,{prior:base,head:source});
 const wrongPair={...pack,scaleVariants:[{...pack.scaleVariants[0],pixel:asset(a,5)},pack.scaleVariants[1]]};
 assert.throws(()=>preparePropPublication(run,rows,claim,wrongPair,{prior:base,head:source}),/PROP_PACKAGE_PROOF_INVALID/);
 assert.equal(proof.scope.operation,'replace-scales');
 const oldTask=buildPlan({...makeRow(payload),officialRequestId,requestType},{replacePropScales:false}).tasks.at(-1);
 const legacy=rows.map(r=>r.task_key===propKeys.publish?{...r,task_type:oldTask.type}:r);
 assert.throws(()=>preparePropPublication(run,legacy,claim,pack,{prior:base,head:source}),/PROP_PACKAGE_PROOF_INVALID/);
 const receipt={...proof,schemaVersion:'ngs.prop-package-publication-receipt.v1',authority:'filex',status:'committed'};
 assert(verifyPropPublicationProof(proof,{manifest:pack,receipt,prior:base}));
 assert.throws(()=>preparePropPublication(run,rows,claim,{...pack,title:'수정'}, {prior:base,head:source}),/PROP_PACKAGE_PROOF_INVALID/);
 const [next,old]=pack.scaleVariants;
 assert.throws(()=>preparePropPublication(run,rows,claim,{...pack,scaleVariants:[next,{...old,spirit:asset(a,20)}]},
  {prior:base,head:source}),/PROP_PACKAGE_PROOF_INVALID/);
});

test('retrying or exhausting a scale has the ordinary workflow status without publishing a partial prop',async t=>{
 const temp=root(t),rs=await server(t,temp,{propGenerateEnabled:true,propMutationsEnabled:true});
 const payload={schemaVersion:'ngs.prop-scale-generate.v1',projectId:'binari',propId:p.propId,source:sourcePin,scalePercents:[5,20,100]};
 const {officialRequestId}=await rs.call('request_submit',{sourceId:'local',requestType:'prop.scale.generate',
  idempotencyKey:'scale-stage-failure',payload});
 const store=new PlannerStore(':memory:',{propGenerateEnabled:true,propMutationsEnabled:true,readPropPublication:async()=>assert.fail('no publish')});
 t.after(()=>store.close());
 assert.equal((await pollOnce(store,rs)).acknowledged,1);
 const update=store.db.prepare(`UPDATE workflow_tasks SET status=?,retry_count=?,result_ref=?
  WHERE official_request_id=? AND task_key=?`);
 update.run('completed',0,propRef('prop_scale_005','a'.repeat(64)),officialRequestId,'prop_scale_005');
 update.run('failed',1,null,officialRequestId,'prop_scale_020');
 assert.equal((await pollOnce(store,rs)).failed,0);
 assert.equal((await rs.call('request_list',{})).requests[0].status,'planned');
 store.db.prepare(`UPDATE workflow_tasks SET retry_count=retry_limit WHERE official_request_id=? AND task_key='prop_scale_020'`)
  .run(officialRequestId);
 assert.equal((await pollOnce(store,rs)).failed,1);
 assert.equal((await rs.call('request_list',{})).requests[0].status,'failed');
});
