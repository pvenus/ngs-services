import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {buildPlan,PlannerStore,pollOnce} from './planner.mjs';
import {canonical,sha} from './envelope.mjs';

const fixture=JSON.parse(readFileSync(new URL('../contracts/motion-preset-convert-2d.v1.fixture.json',import.meta.url),'utf8'));
const directions=['n','ne','e','se','s','sw','w','nw'];
const invalidId='request.official.'+'0'.repeat(64);
const rows=store=>store.db.prepare('SELECT * FROM workflow_tasks ORDER BY rowid').all();
const stages=store=>Object.fromEntries(rows(store).map(t=>[t.task_key,t]));

test('formal motion DAG matches golden rows, all eight predecessors exact, and no direct output writes',()=>{
 const {row,tasks:golden}=fixture,{payload,tasks}=buildPlan(row);
 assert.deepEqual(tasks,golden);assert.equal(payload,canonical(row.payload));
 const [image,...rest]=tasks,final=rest.pop();
 assert.equal(image.key,'motion_2d_image');assert.equal(image.workerType,'image');assert.equal(image.dependsOn,null);
 assert.equal(final.key,'motion_2d_finalize');assert.equal(final.workerType,'animation');assert.equal(final.dependsOn,null);
 assert.deepEqual(rest.map(t=>t.input.direction),directions);
 for(const clip of rest){assert.equal(clip.dependsOn,image.id);assert.equal(clip.workerType,'animation');}
 assert.deepEqual(final.input.dependsOnTaskIds,rest.map(t=>t.id));
 assert(!final.input.dependsOnTaskIds.includes(image.id));
 assert.equal(new Set(tasks.map(t=>t.id)).size,10);
});

test('motion planning persists atomic and immutable fan-in, ACK-gated, and never completes a partial run',async t=>{
 const s=new PlannerStore(':memory:');t.after(()=>s.close());
 const {row,tasks:golden}=fixture;
 const invalid={...row,officialRequestId:invalidId,payload:{...row.payload,variantPresetId:row.payload.sourcePresetId}};
 invalid.payloadSha256=sha(canonical(invalid.payload));
 let acks=[];
 const rs={async call(tool,input){
  if(tool==='request_workflow_list')return {requests:[invalid,row],nextCursor:null};
  acks.push(input);
  if(input.status==='planned')assert.equal(s.db.prepare('SELECT state FROM workflow_runs').get().state,'awaiting_rs');
  return input;
 }};
 const summary=await pollOnce(s,rs);
 assert.equal(summary.invalid,1);assert.equal(summary.staged,1);assert.equal(summary.acknowledged,1);
 assert.deepEqual(acks,[{officialRequestId:row.officialRequestId,status:'planned'}]);
 assert.equal(s.stage(row),false);
 assert.deepEqual(rows(s).map(t=>({id:t.task_id,key:t.task_key,type:t.task_type,workerType:t.worker_type,
  dependsOn:t.depends_on_task_id,input:JSON.parse(t.task_input)})),golden);
 const complete=s.db.prepare("UPDATE workflow_tasks SET status='completed',result_ref=? WHERE task_key=?");
 complete.run('motion.2d.image.'+'1'.repeat(64),'motion_2d_image');
 for(const direction of directions.slice(0,7))complete.run('motion.2d.clip.'+direction+'.'+'2'.repeat(64),'motion_2d_'+direction);
 assert.deepEqual(s.terminalReady(),[]);
 complete.run('motion.2d.clip.nw.'+'3'.repeat(64),'motion_2d_nw');
 assert.deepEqual(s.terminalReady(),[]);
 complete.run('unverified.'+'4'.repeat(64),'motion_2d_finalize');
 assert.deepEqual(s.terminalReady(),[]);
 complete.run('motion.2d.published.'+'4'.repeat(64),'motion_2d_finalize');
 complete.run('motion.2d.clip.sw.'+'3'.repeat(64),'motion_2d_nw');
 assert.deepEqual(s.terminalReady(),[]);
 complete.run('motion.2d.clip.nw.'+'3'.repeat(64),'motion_2d_nw');
 assert.equal((await pollOnce(s,rs)).completed,1);
 assert.deepEqual(acks.at(-1),{officialRequestId:row.officialRequestId,status:'completed'});
 assert.deepEqual(JSON.parse(stages(s).motion_2d_finalize.task_input).dependsOnTaskIds,golden.at(-1).input.dependsOnTaskIds);
});

test('request and planner envelope validators remain byte-identical',()=>{
 const name='motion-preset-convert-2d-envelope.mjs';
 assert.deepEqual(readFileSync(new URL(name,import.meta.url)),readFileSync(new URL('../request-service/'+name,import.meta.url)));
});
