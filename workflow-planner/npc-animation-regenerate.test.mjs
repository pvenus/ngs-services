import assert from 'node:assert/strict';
import test from 'node:test';
import {mkdtempSync,readFileSync,realpathSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {buildPlan,directions,PlannerStore,pollOnce} from './planner.mjs';
import {openOfficialInbox} from '../request-service/official-inbox.mjs';
import {canonical,motionSelectionSlot,sha} from './envelope.mjs';

const fixture=JSON.parse(readFileSync(new URL('../contracts/npc-animation-regenerate.v1.fixture.json',import.meta.url)));
test('exact dependency plan only expands requested native animation and postprocess slots',()=>{
 const {row,plan}=fixture;
 assert.deepEqual(buildPlan(row),plan);
 assert.equal(plan.tasks.length,32);
 for(const p of row.payload.motionSelection)for(const direction of directions){
  const stem='motion_'+motionSelectionSlot(p.presetId)+'_'+direction;
  const animation=plan.tasks.find(t=>t.key==='animation_'+stem),frames=plan.tasks.find(t=>t.key==='frames_'+stem);
  const common={npcId:row.payload.npcId,direction,motion:p.presetId,posePreset:p.posePreset,assetSetSha256:p.assetSetSha256};
  assert.deepEqual(animation.input,{...common,output:'native-png'});
  assert.equal(animation.dependsOn,null);
  assert.deepEqual(frames.input,{...common,input:'native-png'});assert.equal(frames.dependsOn,animation.id);
 }
 for(const bad of [{...row.payload,npcId:'../other'},{...row.payload,character:{}},
  {...row.payload,motionSelection:[row.payload.motionSelection[0],{...row.payload.motionSelection[0],presetId:'RUN-2D'}]}]){
  assert.throws(()=>buildPlan({...row,payload:bad,payloadSha256:sha(canonical(bad))}),/REGENERATE_PAYLOAD_INVALID/);
 }
 assert.throws(()=>buildPlan({...row,payload:{...row.payload,npcId:'character.yeonwoo'}}),/WORKFLOW_PAYLOAD_BINDING_CHANGED/);
});

test('definitive regeneration failure unlocks a new explicit request only after runnable or uncertain lanes are resolved',async t=>{
 const temp=mkdtempSync(join(realpathSync(tmpdir()),'regeneration-failed-'));
 const store=new PlannerStore(join(temp,'workflow.sqlite')),inbox=openOfficialInbox(join(temp,'request-service'));
 t.after(()=>{store.close();inbox.close();rmSync(temp,{recursive:true,force:true});});
 const submit=key=>inbox.register({sourceId:'local',requestType:fixture.row.requestType,idempotencyKey:key,payload:fixture.row.payload});
 const first=submit('first'),id=first.officialRequestId,attempts=[];
 const snapshot=()=>store.db.prepare('SELECT state,reason FROM workflow_runs WHERE official_request_id=?').get(id);
 let dropNextFailedAck=true;
 const rs={async call(tool,args){
  if(tool==='request_workflow_list')return inbox.workflowList(args);
  assert.equal(tool,'request_update_status');
  attempts.push(args);
  const ack=inbox.updateStatus(args);
  if(args.status==='failed'&&dropNextFailedAck){dropNextFailedAck=false;throw Error('fixture lost ACK');}
  return ack;
 }};
 assert.equal((await pollOnce(store,rs)).acknowledged,1);
 const row=store.db.prepare('SELECT task_key,task_id,depends_on_task_id FROM workflow_tasks WHERE official_request_id=? ORDER BY rowid').all(id);
 const [failedAnimation,blockedFrames,retryAnimation,otherFrames]=row;
 assert.equal(blockedFrames.depends_on_task_id,failedAnimation.task_id);
 assert.equal(otherFrames.depends_on_task_id,retryAnimation.task_id);
 const update=store.db.prepare('UPDATE workflow_tasks SET status=?,retry_count=?,updated_at=? WHERE task_id=?');
 const noFailure=async()=>{
  assert.equal((await pollOnce(store,rs)).failed,0);
  assert.equal(snapshot().state,'planned');assert.deepEqual(attempts.map(a=>a.status),['planned']);
  assert.throws(()=>submit('manual'),/OFFICIAL_REQUEST_NPC_ALREADY_REGISTERED/);
 };
 store.db.prepare("UPDATE workflow_tasks SET status='completed' WHERE official_request_id=?").run(id);
 update.run('failed',3,0,failedAnimation.task_id);
 update.run('pending',0,0,blockedFrames.task_id);
 update.run('pending',0,0,retryAnimation.task_id);
 update.run('pending',0,0,otherFrames.task_id);
 await noFailure(); // Pending animation with no predecessor.
 update.run('failed',2,Date.now(),retryAnimation.task_id);
 await noFailure(); // Waiting for the ten-minute retry cooldown.
 update.run('failed',2,0,retryAnimation.task_id);
 await noFailure(); // Currently retry-eligible.
 update.run('running',3,0,retryAnimation.task_id);
 await noFailure(); // Exhausted retry counter cannot turn an unknown claim into failure.
 update.run('completed',3,0,retryAnimation.task_id);
 await noFailure(); // Pending frames can now run.
 update.run('completed',0,0,otherFrames.task_id);

 // Even legal terminal row statuses do not authorize a damaged graph.
 store.db.prepare('UPDATE workflow_tasks SET depends_on_task_id=? WHERE task_id=?').run(retryAnimation.task_id,blockedFrames.task_id);
 await noFailure();
 store.db.prepare('UPDATE workflow_tasks SET depends_on_task_id=? WHERE task_id=?').run(failedAnimation.task_id,blockedFrames.task_id);

 // Request Service commits first. On lost response the same notification is
 // idempotent; old tasks/blocks remain observable and are never requeued.
 const before=store.db.prepare('SELECT * FROM workflow_tasks WHERE official_request_id=? ORDER BY rowid').all(id);
 const missed=await pollOnce(store,rs);assert.equal(missed.failed,0);assert.equal(missed.pollError,true);
 assert.equal(snapshot().state,'planned');assert.equal(inbox.list({status:'failed'}).requests[0].officialRequestId,id);
 assert.equal((await pollOnce(store,rs)).failed,1);
 assert.equal(snapshot().state,'failed');assert.equal(snapshot().reason,'WORKFLOW_TASK_RETRIES_EXHAUSTED');
 assert.deepEqual(store.db.prepare('SELECT * FROM workflow_tasks WHERE official_request_id=? ORDER BY rowid').all(id),before);
 assert.deepEqual(attempts.map(a=>a.status),['planned','failed','failed']);
 assert.equal((await pollOnce(store,rs)).failed,0);
 const manual=submit('manual');
 assert.notEqual(manual.officialRequestId,id);assert.equal(manual.status,'registered');
 assert.equal((await pollOnce(store,rs)).acknowledged,1);
 assert.equal(store.db.prepare('SELECT state FROM workflow_runs WHERE official_request_id=?').get(manual.officialRequestId).state,'planned');
});

test('invalid regeneration row does not prevent later valid intake, and legacy failure is unchanged',async t=>{
 const store=new PlannerStore(':memory:');t.after(()=>store.close());
 const payload={schemaVersion:'ngs.npc-one-stop.v1',projectId:'binari',
  character:{npcId:'npc.fixture',name:'테스트 인물',slug:'fixture',role:'마을 주민',actId:'act1',chapterId:'ch1'},
  intent:'테스트 동작',sourceRefs:['story.ch1.fixture'],includeCombat:false};
 const legacy={...fixture.row,officialRequestId:'request.official.'+sha('legacy'),requestType:'npc.workflow',
  schemaVersion:payload.schemaVersion,payload,payloadSha256:sha(canonical(payload))};
 let feed=true;const statuses=[];
 const rs={async call(tool,args){
  if(tool==='request_workflow_list'){
   const rows=feed?[{...fixture.row,payload:{...fixture.row.payload,motionSelection:[]}},legacy]:[];
   feed=false;return {requests:rows,nextCursor:null};
  }
  statuses.push(args.status);return args;
 }};
 const initial=await pollOnce(store,rs);
 assert.equal(initial.invalid,1);assert.equal(initial.acknowledged,1);assert.equal(initial.pollError,false);
 store.db.exec("UPDATE workflow_tasks SET status='completed'; UPDATE workflow_tasks SET status='failed',retry_count=3 WHERE task_key='frames_walk_n'");
 assert.equal((await pollOnce(store,rs)).failed,0);
 assert.deepEqual(statuses,['planned']);
 assert.equal(store.db.prepare('SELECT state FROM workflow_runs').get().state,'planned');
});

test('animation-only plan supports more than eight pinned presets without bulk truncation',t=>{
 const store=new PlannerStore(':memory:');t.after(()=>store.close());
 const row=fixture.row,payload={...row.payload,motionSelection:Array.from({length:20},(_,i)=>({
  ...row.payload.motionSelection[i%2],presetId:'preset-'+i}))};
 const expanded={...row,payload,payloadSha256:sha(canonical(payload))};
 assert.equal(store.stage(expanded),true);
 assert.equal(store.db.prepare('SELECT COUNT(*) AS n FROM workflow_tasks').get().n,320);
 store.acknowledge(row.officialRequestId);
 store.db.exec("UPDATE workflow_tasks SET status='completed'");
 assert.deepEqual(store.terminalReady(),[{official_request_id:row.officialRequestId}]);
});
