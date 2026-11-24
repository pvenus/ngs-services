import assert from 'node:assert/strict';
import test from 'node:test';
import {buildPlan,PlannerStore,pollOnce} from './planner.mjs';
import {canonical,sha} from './envelope.mjs';
import {cinematicTask,cinematicTaskType,cinematicType,cinematicSchema,cinematicResultRef} from './cinematic-envelope.mjs';

const oid='request.official.'+'a'.repeat(64),sourceSha256='b'.repeat(64);
const p={schemaVersion:cinematicSchema,projectId:'binari',sourceSha256,motionPrompt:'00–02 초\n좌에서 우로 이동'};
const request=(payload=p)=>({officialRequestId:oid,projectId:'binari',requestType:cinematicType,schemaVersion:cinematicSchema,
 payload,payloadSha256:sha(canonical(payload)),status:'registered',registeredAt:10});
const ref=cinematicResultRef(oid);
const m={schemaVersion:'ngs.cinematic-result.v1',projectId:'binari',officialRequestId:oid,sha256:'c'.repeat(64),
 sourceSha256,bytes:12000,mimeType:'video/mp4',width:512,height:352,fps:24,frameCount:45};

test('cinematic persists exactly one animation task with no matting/postprocess lane',()=>{
 const row=request();
 const out=buildPlan(row);
 assert.deepEqual(out,{payload:canonical(p),tasks:[{id:'workflow.task.'+sha(canonical([oid,cinematicTask])),
  key:cinematicTask,type:cinematicTaskType,workerType:'animation',dependsOn:null,input:{sourceSha256,motionPrompt:p.motionPrompt}}]});
 for(const value of [{...p,sourceUrl:'https://example.invalid/a.png'},{...p,sourceSha256:'../path'},
  {...p,motionPrompt:'bad embedding:local-file'},{...p,motionPrompt:'가'.repeat(667)}, {...p,removeBackground:true}])
  assert.throws(()=>buildPlan(request(value)),/NGS_CINEMATIC_PAYLOAD_INVALID/);
 const disabled=new PlannerStore(':memory:');
 try{assert.throws(()=>disabled.stage(row),/WORKFLOW_CINEMATIC_UNAVAILABLE/);}finally{disabled.close();}
});

test('RS remains planned until durable full-scene MP4 proof matches exact source and official request',async()=>{
 let proof=null;const store=new PlannerStore(':memory:',{cinematicEnabled:true,readCinematicPublication:async()=>proof});
 try{
  store.stage(request());store.acknowledge(oid);
  store.db.prepare("UPDATE workflow_tasks SET status='completed',result_ref=? WHERE official_request_id=?").run(ref,oid);
  const statuses=[];
  const rs={call:async(name,args)=>{
   if(name==='request_workflow_list')return {requests:[],nextCursor:null};
   statuses.push(args.status);return args;
  }};
  let result=await pollOnce(store,rs);
  assert.deepEqual(statuses,[]);assert.equal(result.completed,0);assert.equal(result.pollError,true);
  proof={ok:true,resultRef:ref,manifest:{...m,sourceSha256:'0'.repeat(64)}};
  result=await pollOnce(store,rs);assert.equal(result.completed,0);
  proof={ok:true,resultRef:ref,manifest:m};
  result=await pollOnce(store,rs);assert.equal(result.completed,1);assert.deepEqual(statuses,['completed']);
  assert.equal(store.db.prepare('SELECT reason FROM workflow_runs').get().reason,'RS_COMPLETED');
 }finally{store.close();}
});
