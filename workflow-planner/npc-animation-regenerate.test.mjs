import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {buildPlan,directions} from './planner.mjs';
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
