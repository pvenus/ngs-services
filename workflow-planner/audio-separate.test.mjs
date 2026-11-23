import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {buildPlan,PlannerStore,pollOnce} from './planner.mjs';
import {canonical,sha} from './envelope.mjs';
import {validateAudioSeparate} from './audio-separate-envelope.mjs';

const {row,plan,submit}=JSON.parse(readFileSync(new URL('../contracts/audio-separate.v1.fixture.json',import.meta.url)));
test('pinned separation expands only one audio task with exact source pin and never rewrites original',async t=>{
 assert.deepEqual(buildPlan(row),plan);
 assert.equal(validateAudioSeparate(submit.payload,'binari'),canonical(submit.payload));
 assert.equal(plan.tasks.length,1);
 assert.deepEqual(plan.tasks[0],{id:'workflow.task.'+sha(canonical([row.officialRequestId,'audio_separate'])),
  key:'audio_separate',type:'audio.separate',workerType:'audio',dependsOn:null,
  input:{sourceOfficialRequestId:row.payload.sourceOfficialRequestId,sourceSha256:row.payload.sourceSha256}});
 assert.deepEqual(readFileSync(new URL('./audio-separate-envelope.mjs',import.meta.url)),
  readFileSync(new URL('../request-service/audio-separate-envelope.mjs',import.meta.url)));
 const store=new PlannerStore(':memory:');t.after(()=>store.close());
 const calls=[];let next=true;
 const rs={async call(name,args){
  if(name==='request_workflow_list'){
   const requests=next?[{...row,payload:{...row.payload,sourceSha256:'invalid'}},row]:[];
   next=false;return {requests,nextCursor:null};
  }
  assert.equal(name,'request_update_status');calls.push(args);return args;
 }};
 const first=await pollOnce(store,rs);
 assert.equal(first.invalid,1);assert.equal(first.acknowledged,1);
 assert.equal(store.db.prepare('SELECT count(*) n FROM workflow_runs').get().n,1);
 assert.equal(store.db.prepare('SELECT official_request_id FROM workflow_runs').get().official_request_id,row.officialRequestId);
 store.db.exec("UPDATE workflow_tasks SET status='completed'");
 assert.equal((await pollOnce(store,rs)).completed,1);
 assert.deepEqual(calls,[{officialRequestId:row.officialRequestId,status:'planned'},{officialRequestId:row.officialRequestId,status:'completed'}]);
});

test('separation rejects changed pin, self-source, unsafe IDs and extra fields',()=>{
 const retie=patch=>{const payload={...row.payload,...patch};return {...row,payload,payloadSha256:sha(canonical(payload))};};
 for(const patch of [{sourceOfficialRequestId:row.officialRequestId},{sourceOfficialRequestId:'../audio.wav'},
  {sourceOfficialRequestId:row.payload.sourceOfficialRequestId+'\n'},{sourceSha256:'A'.repeat(64)},
  {sourceSha256:'a'.repeat(63)+'\n'},{filePath:'unsafe'},{schemaVersion:'ngs.audio-separate.v2'}])
  assert.throws(()=>buildPlan(retie(patch)),/NGS_AUDIO_SEPARATE_PAYLOAD_INVALID/);
 assert.throws(()=>buildPlan({...row,payload:{...row.payload,sourceSha256:'b'.repeat(64)}}),/WORKFLOW_PAYLOAD_BINDING_CHANGED/);
});
