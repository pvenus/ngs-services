import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {buildPlan,PlannerStore,directions} from './planner.mjs';
import {canonical,sha,motionSelectionSlot,isReplaceSchema} from './envelope.mjs';

const {cases}=JSON.parse(readFileSync(new URL('../contracts/npc-one-stop-motion-selection.v1.fixture.json',import.meta.url),'utf8'));
const media=new Set(['npc.animation-native-png','npc.frames-postprocess']);
const hashRow=(row,payload)=>({...row,payload,payloadSha256:sha(canonical(payload))});

test('selected create/replacement DAGs match shared golden: cross-poses share directional appearance; outputs stay preset-specific',t=>{
 const s=new PlannerStore(':memory:');t.after(()=>s.close());
 for(const {row,tasks:golden} of cases){
  const {tasks,payload}=buildPlan(row);
  assert.deepEqual(tasks,golden);assert.equal(payload,canonical(row.payload));
  assert.equal(tasks.some(x=>x.type==='npc.delete-folder'),isReplaceSchema(row.schemaVersion));
  assert(!tasks.some(x=>x.key.startsWith('portrait_combat_')));
  const byKey=Object.fromEntries(tasks.map(t=>[t.key,t]));
  const selected=row.payload.motionSelection;
  assert.equal(tasks.filter(t=>media.has(t.type)).length,selected.length*16);
  for(const direction of directions)for(const {presetId,posePreset,assetSetSha256} of selected){
   const slot=motionSelectionSlot(presetId),a=byKey['animation_motion_'+slot+'_'+direction],f=byKey['frames_motion_'+slot+'_'+direction];
   const data={npcId:row.payload.character.npcId,direction,motion:presetId,posePreset,assetSetSha256};
   assert.equal(a.dependsOn,byKey['portrait_'+direction].id);
   assert.deepEqual(a.input,{...data,output:'native-png'});
   assert.equal(f.dependsOn,a.id);assert.deepEqual(f.input,{...data,input:'native-png'});
  }
  assert.equal(s.stage(row),true);assert.equal(s.stage(row),false);
  assert.equal(s.db.prepare('SELECT count(*) AS n FROM workflow_tasks WHERE official_request_id=?').get(row.officialRequestId).n,tasks.length);
 }
});

test('selected subset is authoritative, no injected defaults; legacy supported graph does not acquire new binding fields',()=>{
 const {row}=cases[0],{motionSelection,...common}=row.payload;
 const picked=buildPlan(hashRow(row,{...common,motionSelection:[motionSelection[0]]})).tasks.filter(t=>media.has(t.type));
 assert.equal(picked.length,16);assert(picked.every(task=>task.input.motion===motionSelection[0].presetId));
 const legacySchema='ngs.npc-one-stop.v1',legacyPayload={...common,schemaVersion:legacySchema,
  includeCombat:false,motionPresets:{walk:'walk',run:'run'}};
 const legacy=buildPlan(hashRow({...row,schemaVersion:legacySchema},legacyPayload)).tasks;
 assert.deepEqual(legacy.filter(t=>t.type==='npc.animation-native-png').slice(0,2).map(t=>[t.key,t.input]),[
  ['animation_walk_n',{npcId:common.character.npcId,direction:'n',motion:'walk',output:'native-png'}],
  ['animation_run_n',{npcId:common.character.npcId,direction:'n',motion:'run',output:'native-png'}]]);
 assert.throws(()=>buildPlan(hashRow({...row,schemaVersion:legacySchema},{...legacyPayload,includeCombat:true,combatMotionPresets:['attack-flat']})),
  /WORKFLOW_COMBAT_NOT_SUPPORTED/);
 assert.throws(()=>buildPlan({...row,payload:{...row.payload,motionSelection:motionSelection.toReversed()}}),/WORKFLOW_PAYLOAD_BINDING_CHANGED/);
 const large=buildPlan(hashRow(row,{...common,motionSelection:Array.from({length:20},(_,i)=>({...motionSelection[0],presetId:'pose-'+i}))}));
 assert.equal(large.tasks.filter(task=>media.has(task.type)).length,320);
 assert(new Set(large.tasks.map(task=>task.id)).size===large.tasks.length);
});

test('request and planner one-stop envelopes have identical wire validators',()=>{
 assert.deepEqual(readFileSync(new URL('./envelope.mjs',import.meta.url)),
  readFileSync(new URL('../request-service/one-stop-envelope.mjs',import.meta.url)));
});

test('legacy full replacement plans only the exact character owner and new creation cannot use it',()=>{
 const {row}=cases.find(test=>isReplaceSchema(test.row.schemaVersion));
 const payload={...row.payload,character:{...row.payload.character,npcId:'character.yeonwoo',slug:'yeonwoo',actId:'act01',chapterId:'chapter01'}};
 const {tasks}=buildPlan(hashRow(row,payload));
 assert.equal(tasks[0].type,'npc.delete-folder');
 assert.equal(tasks[1].dependsOn,tasks[0].id);
 assert(tasks.every(t=>t.input.npcId==='character.yeonwoo'));
 assert.throws(()=>buildPlan(hashRow(row,{...payload,character:{...payload.character,slug:'seojin'}})),/ONE_STOP_PAYLOAD_INVALID/);
 const create={...payload,schemaVersion:'ngs.npc-one-stop.selection.v1'};
 assert.throws(()=>buildPlan(hashRow({...row,schemaVersion:create.schemaVersion},create)),/ONE_STOP_PAYLOAD_INVALID/);
});
