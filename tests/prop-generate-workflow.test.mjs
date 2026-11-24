import assert from 'node:assert/strict';
import {mkdtempSync,realpathSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import test from 'node:test';
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

test('real MCP ingress/Planner: one pack only after same-run complete proof and independent Filex receipt readback',async t=>{
 const temp=root(t),rs=await server(t,temp,{propGenerateEnabled:true,propGenerateMotionEnabled:true});
 const payload={...p,motion:'subtle'};
 const {officialRequestId}=await rs.call('request_submit',command(payload));
 let proof=null,reads=0;
 const store=new PlannerStore(join(temp,'planner.sqlite'),{propGenerateEnabled:true,propGenerateMotionEnabled:true,
  readPropPublication:async()=>{reads++;return proof;}});
 t.after(()=>store.close());
 assert.equal((await pollOnce(store,rs)).acknowledged,1);
 assert.equal((await pollOnce(store,rs)).staged,0);
 const row={...makeRow(payload),officialRequestId},run={...row,state:'planned',reason:null},tasks=buildPlan(row).tasks,final=tasks.at(-1);
 const rows=()=>store.db.prepare('SELECT * FROM workflow_tasks WHERE official_request_id=?').all(officialRequestId);
 const evidence=Object.fromEntries(tasks.slice(0,-1).map((x,i)=>[x.key,(i+1).toString(16).repeat(64)]));
 const put=store.db.prepare('UPDATE workflow_tasks SET status=?,result_ref=?,updated_at=9 WHERE task_id=?');
 const claim={officialRequestId,projectId:p.projectId,taskId:final.id,payloadSha256:run.payloadSha256,claimStamp:9};
 const media=(key,width,height,animated=false)=>({assetId:evidence[key]+'.png',sha256:evidence[key],bytes:600,width,height,
  mediaType:animated?'image/apng':'image/png',frameDurationsMs:animated?[100,100]:[]});
 const manifest={schemaVersion:'ngs.prop-package.v1',officialRequestId,projectId:'binari',propId:p.propId,
  requestSha256:run.payloadSha256,designContextSha256:p.designContextSha256,revision:1,title:'등롱',parentSha256:null,
  media:{detail:media(propKeys.detail,512,512),pixel:media(propKeys.pixel,128,128)},
  animations:[{id:'initial',title:'미세 움직임',officialRequestId,requestSha256:run.payloadSha256,media:{
   detail:media(propKeys.animDetail,512,512,true),pixel:media(propKeys.animPixel,128,128,true)}}],
  interaction:{pixelOpen:{action:'open',x:1,y:2,width:15,height:15},detailTrigger:{action:'activate',x:2,y:3,width:80,height:80}}};
 put.run('running',null,final.id);
 for(const task of tasks.slice(0,-2))put.run('completed',propRef(task.key,evidence[task.key]),task.id);
 assert.throws(()=>preparePropPublication(run,rows(),claim,manifest),/PROP_PACKAGE_PROOF_INVALID/);
 assert.equal((await pollOnce(store,rs)).completed,0);assert.equal(reads,0);
 const pixel=tasks.at(-2);put.run('completed',propRef(pixel.key,evidence[pixel.key]),pixel.id);
 assert.throws(()=>preparePropPublication(run,rows(),{...claim,claimStamp:8},manifest),/PROP_PACKAGE_PROOF_INVALID/);
 const wrongMedia={...manifest,animations:manifest.animations.map(clip=>({...clip,media:{...clip.media,pixel:{...clip.media.pixel,frameDurationsMs:[80,120]}}}))};
 assert.throws(()=>preparePropPublication(run,rows(),claim,wrongMedia),/PROP_PACKAGE_PROOF_INVALID/);
 const candidate=preparePropPublication(run,rows(),claim,manifest);
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
 store.db.prepare("UPDATE workflow_tasks SET status='failed',retry_count=3 WHERE task_key=? AND official_request_id=?").run(propKeys.design,officialRequestId);
 assert.equal((await pollOnce(store,rs)).failed,1);
 assert.equal((await rs.call('request_list',{})).requests[0].status,'failed');
 assert.equal(store.db.prepare("SELECT count(*) AS n FROM workflow_tasks WHERE status='pending'").get().n,4);
});
