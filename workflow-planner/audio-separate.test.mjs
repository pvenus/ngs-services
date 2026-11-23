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

test('only an exact failed and exhausted separation settles; running or retryable audio stays planned',async t=>{
 const store=new PlannerStore(':memory:');t.after(()=>store.close());
 const bgm={schemaVersion:'ngs.audio-generate.v1',projectId:'binari',kind:'bgm',title:'fixture',
  conceptId:'sound',brief:'fixture brief',text:'',durationSeconds:20};
 const bgmRow={...row,officialRequestId:row.payload.sourceOfficialRequestId,requestType:'audio.generate',
  schemaVersion:bgm.schemaVersion,payload:bgm,payloadSha256:sha(canonical(bgm))};
 for(const request of [row,bgmRow]){store.stage(request);store.acknowledge(request.officialRequestId);}
 const calls=[];let loseAck=true;
 const rs={async call(method,args){
  if(method==='request_workflow_list')return {requests:[],nextCursor:null};
  assert.equal(method,'request_update_status');
  assert.deepEqual(args,{officialRequestId:row.officialRequestId,status:'failed'});
  calls.push(args);
  if(loseAck){loseAck=false;throw Error('fixture lost failed ACK');}
  return args;
 }};
 const snapshot=id=>store.db.prepare('SELECT state,reason FROM workflow_runs WHERE official_request_id=?').get(id);
 const update=store.db.prepare('UPDATE workflow_tasks SET status=?,retry_count=?,result_ref=?,task_input=? WHERE task_id=?');
 const [task]=plan.tasks,correct=canonical(task.input);
 const set=(status,retries,result=null,input=correct)=>update.run(status,retries,result,input,task.id);
 store.db.prepare("UPDATE workflow_tasks SET status='failed',retry_count=3 WHERE official_request_id=?").run(bgmRow.officialRequestId);
 for(const state of [['pending',0],['failed',2],['running',3],['failed',3,'uncertain-result'],
  ['failed',3,null,canonical({...task.input,sourceSha256:'b'.repeat(64)})]]){
  set(...state);
  const ignored=await pollOnce(store,rs);assert.equal(ignored.failed,0);assert.equal(ignored.pollError,false);
  assert.equal(snapshot(row.officialRequestId).state,'planned');
 }
 set('failed',3);
 // A lookalike graph with an additional task is not eligible either.
 const taskId='workflow.task.'+sha('extra task');
 store.db.prepare(`INSERT INTO workflow_tasks(task_id,official_request_id,task_key,task_type,worker_type,task_input,updated_at)
  VALUES(?,?,'extra','audio.separate','audio','{}',0)`).run(taskId,row.officialRequestId);
 const ignored=await pollOnce(store,rs);assert.equal(ignored.failed,0);assert.equal(ignored.pollError,false);
 assert.deepEqual(calls,[]);
 store.db.prepare('DELETE FROM workflow_tasks WHERE task_id=?').run(taskId);
 const saved=store.db.prepare('SELECT * FROM workflow_tasks WHERE task_id=?').get(task.id);
 const missed=await pollOnce(store,rs);
 assert.equal(missed.failed,0);assert.equal(missed.pollError,true);
 assert.equal(snapshot(row.officialRequestId).state,'planned');
 assert.equal((await pollOnce(store,rs)).failed,1);
 assert.deepEqual({...snapshot(row.officialRequestId)},{state:'failed',reason:'WORKFLOW_TASK_RETRIES_EXHAUSTED'});
 assert.equal(snapshot(bgmRow.officialRequestId).state,'planned');
 assert.deepEqual(store.db.prepare('SELECT * FROM workflow_tasks WHERE task_id=?').get(task.id),saved);
 assert.equal((await pollOnce(store,rs)).failed,0);
 assert.equal(calls.length,2);
});
