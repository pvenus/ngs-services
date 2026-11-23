import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {buildPlan,directions,PlannerStore,pollOnce} from './planner.mjs';
import {canonical,sha,motionPoseRegistry,motionSelectionSlot} from './envelope.mjs';

const read=name=>JSON.parse(readFileSync(new URL('../contracts/'+name,import.meta.url)));
const {cases}=read('npc-one-stop-motion-selection.v2.fixture.json');
const old=read('npc-one-stop-motion-selection.v1.fixture.json');
const regen=read('npc-animation-regenerate.v2.fixture.json');
const oldRegen=read('npc-animation-regenerate.v1.fixture.json');
const byKey=tasks=>new Map(tasks.map(t=>[t.key,t]));
const bodyOf=(row,patch)=>{
 const payload={...row.payload,...patch};
 return {...row,schemaVersion:payload.schemaVersion,payload,payloadSha256:sha(canonical(payload))};
};

test('frozen full v2 graphs use the registered look for each exact pose and lane, v1 graphs stay byte-compatible',()=>{
 for(const {row,tasks} of cases){
  assert.deepEqual(buildPlan(row).tasks,tasks);
  assert.deepEqual(buildPlan(row,{pixelPortraits:false}).tasks,tasks);
 }
 for(const {row,tasks} of old.cases)assert.deepEqual(buildPlan(row).tasks,tasks);
 const [mixed,idle]=cases, maps=byKey(mixed.tasks), idleOnly=byKey(idle.tasks);
 assert.equal(mixed.tasks.length,75);
 assert.equal(idle.tasks.length,41);
 assert(!idle.tasks.some(task=>task.key.includes('combat')));
 assert.equal(idleOnly.get('preset').dependsOn,idleOnly.get('delete').id);
 assert(idle.tasks.every(task=>task.input.npcId==='character.yeonwoo'));
 for(const [pose,lane] of Object.entries(motionPoseRegistry)){
  assert.equal(maps.get(lane.planKey).dependsOn,maps.get('planning_representative').id);
  assert.equal(maps.get(lane.referenceKey).dependsOn,maps.get(lane.planKey).id);
  const center=maps.get('portrait_pixel_'+pose+'_reference');
  assert.equal(center.dependsOn,maps.get(lane.referenceKey).id);
  assert.deepEqual(center.input,{npcId:mixed.row.payload.character.npcId,posePreset:pose,direction:null});
  for(const d of directions){
   const raw=maps.get(lane.directionPrefix+d),pixel=maps.get('portrait_pixel_'+pose+'_'+d);
   assert.equal(raw.dependsOn,center.id);
   assert.equal(raw.input.imageStyle,'pixel-sprite.v1');
   assert.equal(pixel.dependsOn,raw.id);
   const motion=mixed.row.payload.motionSelection.find(x=>x.posePreset===pose),slot=motionSelectionSlot(motion.presetId);
   const animation=maps.get('animation_motion_'+slot+'_'+d),frames=maps.get('frames_motion_'+slot+'_'+d);
   assert.equal(animation.dependsOn,pixel.id);
   assert.equal(frames.dependsOn,animation.id);
   assert.deepEqual([animation.input.direction,animation.input.motion,animation.input.posePreset],[d,motion.presetId,pose]);
  }
 }
});

test('several combat presets share one pose-specific portrait chain; idle baseline is still generated',()=>{
 const {row}=cases[0],combat=row.payload.motionSelection[0];
 const selectedOnly=bodyOf(row,{motionSelection:[combat,{...combat,presetId:'second-combat'}]});
 const plan=buildPlan(selectedOnly),tasks=byKey(plan.tasks);
 assert(tasks.has('planning_idle'));assert(tasks.has('planning_combat'));
 assert(!plan.tasks.some(t=>t.type==='npc.animation-native-png'&&t.input.posePreset==='idle'));
 assert.equal(plan.tasks.filter(t=>t.type==='npc.portrait-combat-direction').length,8);
 for(const d of directions){
  const animations=plan.tasks.filter(t=>t.type==='npc.animation-native-png'&&t.input.direction===d);
  assert.equal(animations.length,2);
  assert(animations.every(t=>t.dependsOn===tasks.get('portrait_pixel_combat_'+d).id));
 }
 assert.throws(()=>buildPlan(bodyOf(row,{motionSelection:[{...combat,posePreset:'unregistered'}]})),/ONE_STOP_PAYLOAD_INVALID/);
});

test('animation-only v2 keeps no-portrait graph shape, and new full v2 waits for visible pixel result',async t=>{
 assert.deepEqual(buildPlan(regen.row),regen.plan);
 assert.deepEqual(buildPlan(oldRegen.row),oldRegen.plan);
 assert.equal(regen.plan.tasks.length,oldRegen.plan.tasks.length);
 assert(regen.plan.tasks.filter(t=>t.workerType==='animation').every(t=>t.dependsOn===null));
 assert(regen.plan.tasks.every(t=>['animation','postprocess'].includes(t.workerType)));
 const store=new PlannerStore(':memory:');t.after(()=>store.close());
 const {row}=cases[0];
 const calls=[];let feed=true;
 const rs={async call(method,args){
  if(method==='request_workflow_list'){
   const requests=feed?[row]:[];feed=false;return {requests,nextCursor:null};
  }
  assert.equal(method,'request_update_status');calls.push(args);return args;
 }};
 assert.equal((await pollOnce(store,rs)).acknowledged,1);
 const done=store.db.prepare("UPDATE workflow_tasks SET status='completed' WHERE task_key=?");
 const next=key=>store.db.prepare(`SELECT t.task_key FROM workflow_tasks t
  JOIN workflow_tasks p ON p.task_id=t.depends_on_task_id AND p.official_request_id=t.official_request_id
  WHERE t.task_key=? AND t.status='pending' AND p.status='completed'`).get(key)?.task_key;
 const combat=byKey(cases[0].tasks).get('portrait_pixel_combat_ne');
 for(const motion of row.payload.motionSelection){
  const suffix=motionSelectionSlot(motion.presetId)+'_ne',key='animation_motion_'+suffix;
  assert.equal(next(key),undefined);
  done.run(motion.posePreset==='combat'?'portrait_combat_ne':'portrait_ne');
  assert.equal(next(key),undefined);
  done.run(motion.posePreset==='combat'?combat.key:'portrait_pixel_idle_ne');
  assert.equal(next(key),key);
 }
 store.db.exec("UPDATE workflow_tasks SET status='completed' WHERE task_key<>'portrait_pixel_combat_n'");
 assert.equal((await pollOnce(store,rs)).completed,0);
 done.run('portrait_pixel_combat_n');
 assert.equal((await pollOnce(store,rs)).completed,1);
 assert.deepEqual(calls.map(x=>x.status),['planned','completed']);
});
