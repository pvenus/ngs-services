import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {buildPlan,PlannerStore,pollOnce} from './planner.mjs';
import {canonical,sha} from './envelope.mjs';

const load=name=>JSON.parse(readFileSync(new URL('../contracts/motion-preset-convert-2d.'+name+'.fixture.json',import.meta.url),'utf8'));
const fixture=load('split'),historical=load('v1');
const directions=['n','ne','e','se','s','sw','w','nw'];
const invalidId='request.official.'+'0'.repeat(64);
const rows=store=>store.db.prepare('SELECT * FROM workflow_tasks ORDER BY rowid').all();
const stages=store=>Object.fromEntries(rows(store).map(t=>[t.task_key,t]));
const setup=t=>{const s=new PlannerStore(':memory:');t.after(()=>s.close());return s;};
const setCompleted=s=>s.db.prepare("UPDATE workflow_tasks SET status='completed',result_ref=? WHERE task_key=?");
const ref=(type,direction,letter)=>'motion.2d.'+type+'.'+(direction?direction+'.':'')+letter.repeat(64);

test('formal motion split matches immutable golden rows and unchanged request envelope',()=>{
 const {row,tasks:golden}=fixture,{payload,tasks}=buildPlan(row);
 assert.deepEqual(row,historical.row);
 assert.deepEqual(tasks,golden);assert.equal(payload,canonical(row.payload));
 const [image,...rest]=tasks,final=rest.pop();
 const frame=rest.slice(0,8),encode=rest.slice(8);
 assert.deepEqual([image.key,image.type,image.workerType,image.dependsOn],
  ['motion_2d_image','motion-preset.convert-2d.image','image',null]);
 assert.deepEqual([final.key,final.type,final.workerType,final.dependsOn],
  ['motion_2d_finalize','motion-preset.convert-2d.finalize','postprocess',null]);
 assert.deepEqual(frame.map(x=>x.input.direction),directions);
 assert.deepEqual(encode.map(x=>x.input.direction),directions);
 for(const [i,d] of directions.entries()){
  assert.deepEqual([frame[i].key,frame[i].type,frame[i].workerType,frame[i].dependsOn],
   ['motion_2d_frames_'+d,'motion-preset.convert-2d.frames','animation',image.id]);
  assert.deepEqual([encode[i].key,encode[i].type,encode[i].workerType,encode[i].dependsOn],
   ['motion_2d_encode_'+d,'motion-preset.convert-2d.encode','postprocess',frame[i].id]);
 }
 assert.deepEqual(final.input.dependsOnTaskIds,encode.map(t=>t.id));
 assert.equal(new Set(tasks.map(t=>t.id)).size,18);
});

test('each CPU direction becomes eligible with only its own finished GPU lane',t=>{
 const s=setup(t),{row}=fixture;
 s.stage(row);
 const runnable=s.db.prepare(`SELECT t.task_key FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
  JOIN workflow_tasks parent ON parent.official_request_id=t.official_request_id AND parent.task_id=t.depends_on_task_id
  WHERE t.worker_type='postprocess' AND t.task_type='motion-preset.convert-2d.encode'
  AND r.state='planned' AND t.status='pending' AND parent.status='completed' ORDER BY t.task_key`);
 const keys=()=>runnable.all().map(t=>t.task_key),complete=setCompleted(s);
 complete.run(ref('image',null,'1'),'motion_2d_image');
 assert.deepEqual(keys(),[]);
 complete.run(ref('frames','e','2'),'motion_2d_frames_e');
 assert.deepEqual(keys(),[]); // Still awaiting RS acknowledgement.
 s.acknowledge(row.officialRequestId);
 assert.deepEqual(keys(),['motion_2d_encode_e']);
 complete.run(ref('frames','nw','3'),'motion_2d_frames_nw');
 assert.deepEqual(keys(),['motion_2d_encode_e','motion_2d_encode_nw']);
 assert.equal(stages(s).motion_2d_frames_n.status,'pending');
 assert.equal(stages(s).motion_2d_finalize.status,'pending');
});

test('planning stages 18 rows atomically and aggregates only the exact eight encoded receipts',async t=>{
 const s=setup(t);
 const {row,tasks:golden}=fixture;
 const invalid={...row,officialRequestId:invalidId,payload:{...row.payload,variantPresetId:row.payload.sourcePresetId}};
 invalid.payloadSha256=sha(canonical(invalid.payload));
 const acks=[];
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
 const complete=setCompleted(s);
 complete.run(ref('image',null,'1'),'motion_2d_image');
 for(const d of directions)complete.run(ref('frames',d,'2'),'motion_2d_frames_'+d);
 for(const d of directions.slice(0,7))complete.run(ref('clip',d,'3'),'motion_2d_encode_'+d);
 complete.run(ref('published',null,'4'),'motion_2d_finalize');
 assert.deepEqual(s.terminalReady(),[]);
 complete.run(ref('clip','sw','3'),'motion_2d_encode_nw');
 assert.deepEqual(s.terminalReady(),[]); // Right type, wrong direction.
 complete.run(ref('clip','nw','3'),'motion_2d_encode_nw');
 complete.run(ref('clip','nw','2'),'motion_2d_frames_nw');
 assert.deepEqual(s.terminalReady(),[]); // MP4 is not a PNG bundle receipt.
 complete.run(ref('frames','nw','2'),'motion_2d_frames_nw');
 const final=stages(s).motion_2d_finalize;
 const patch=s.db.prepare('UPDATE workflow_tasks SET task_input=? WHERE task_id=?');
 patch.run(canonical({...golden.at(-1).input,dependsOnTaskIds:golden.at(-1).input.dependsOnTaskIds.toReversed()}),final.task_id);
 assert.deepEqual(s.terminalReady(),[]);
 patch.run(final.task_input,final.task_id);
 s.db.prepare(`INSERT INTO workflow_tasks
  (task_id,official_request_id,task_key,task_type,worker_type,task_input,updated_at,status)
  VALUES ('extra',?,'extra','extra','image','{}',0,'completed')`).run(row.officialRequestId);
 assert.deepEqual(s.terminalReady(),[]); // Row read limit must not hide extra tasks.
 s.db.prepare("DELETE FROM workflow_tasks WHERE task_id='extra'").run();
 assert.equal((await pollOnce(s,rs)).completed,1);
 assert.deepEqual(acks.at(-1),{officialRequestId:row.officialRequestId,status:'completed'});
});

test('persisted historical v1 graph is not rewritten on replay and exact receipts still project completion',t=>{
 const s=setup(t),{row,tasks}=historical;
 s.db.prepare("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,?,'planned',?,NULL)")
  .run(row.officialRequestId,row.projectId,row.requestType,row.schemaVersion,canonical(row.payload),row.payloadSha256,row.registeredAt,1);
 const insert=s.db.prepare(`INSERT INTO workflow_tasks
  (task_id,official_request_id,task_key,task_type,worker_type,task_input,depends_on_task_id,updated_at)
  VALUES(?,?,?,?,?,?,?,1)`);
 for(const task of tasks)insert.run(task.id,row.officialRequestId,task.key,task.type,task.workerType,
  canonical(task.input),task.dependsOn);
 s.db.prepare("UPDATE workflow_tasks SET status='running',updated_at=99 WHERE task_key='motion_2d_n'").run();
 const before=rows(s);
 assert.equal(s.stage(row),false);
 assert.deepEqual(rows(s),before);
 assert.deepEqual(s.terminalReady(),[]);
 const complete=setCompleted(s);
 complete.run(ref('image',null,'1'),'motion_2d_image');
 for(const d of directions)complete.run(ref('clip',d,'2'),'motion_2d_'+d);
 complete.run(ref('published',null,'4'),'motion_2d_finalize');
 assert.deepEqual(s.terminalReady(),[{official_request_id:row.officialRequestId}]);
 assert.equal(rows(s).length,10);
});

test('request and planner envelope validators remain byte-identical',()=>{
 const name='motion-preset-convert-2d-envelope.mjs';
 assert.deepEqual(readFileSync(new URL(name,import.meta.url)),readFileSync(new URL('../request-service/'+name,import.meta.url)));
});
