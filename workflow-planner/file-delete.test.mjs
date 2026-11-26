import test from 'node:test';
import assert from 'node:assert/strict';
import {buildPlan} from './planner.mjs';
import {canonical,sha} from './envelope.mjs';
test('pinned prop delete plans one physical folder, no grouped or stale counterpart',()=>{
 const target={root:'canonical-files/binari/props',path:'lantern',kind:'directory'};
 const expected={revision:2,sha256:'a'.repeat(64)};
 const payload={schemaVersion:'ngs.file-delete.v1',projectId:'binari',target,expected};
 const officialRequestId='request.official.'+'b'.repeat(64);
 const row={officialRequestId,requestType:'file.delete',schemaVersion:payload.schemaVersion,projectId:'binari',
  payload,payloadSha256:sha(canonical(payload)),status:'registered',registeredAt:1};
 assert.deepEqual(buildPlan(row),{payload:canonical(payload),tasks:[
  {id:'workflow.task.'+sha(canonical([officialRequestId,'file_delete'])),key:'file_delete',
   type:'file.delete',workerType:'file',dependsOn:null,input:{target,expected}}]});
 assert.throws(()=>buildPlan({...row,payload:{...payload,target:{...target,kind:'file'}}}));
});

test('NPC plans one physical target pinned to the published creation task',()=>{
 const target={root:'canonical-files/binari/npcs',path:'npc.lantern',kind:'directory'};
 const expected={creationTaskId:'workflow.task.'+'a'.repeat(64)};
 const payload={schemaVersion:'ngs.file-delete.v1',projectId:'binari',target,expected};
 const officialRequestId='request.official.'+'b'.repeat(64);
 const row={officialRequestId,requestType:'file.delete',schemaVersion:payload.schemaVersion,projectId:'binari',
  payload,payloadSha256:sha(canonical(payload)),status:'registered',registeredAt:1};
 assert.deepEqual(buildPlan(row).tasks.map(({type,workerType,input})=>({type,workerType,input})),
  [{type:'file.delete',workerType:'file',input:{target,expected}}]);
});

test('background flat pair is planned as two independent one-file requests',()=>{
 const root='canonical-files/binari/cinematics/backgrounds',hash='e'.repeat(64);
 const expected={sourceRequestId:'request.official.'+'a'.repeat(64)};
 const payload={schemaVersion:'ngs.file-delete.v1',projectId:'binari',expected};
 const plan=(target,i)=>{const p={...payload,target};return buildPlan({
  officialRequestId:'request.official.'+i.repeat(64),requestType:'file.delete',schemaVersion:payload.schemaVersion,
  projectId:'binari',payload:p,payloadSha256:sha(canonical(p)),status:'registered',registeredAt:1});};
 for(const [extension,i] of [['json','b'],['png','c']]){
  const target={root,path:hash+'.'+extension,kind:'file'};
  const result=plan(target,i);
  assert.equal(result.tasks.length,1);
  assert.deepEqual(result.tasks[0].input,{target,expected});
 }
 assert.throws(()=>plan({root,path:hash,kind:'directory'},'d'));
 assert.throws(()=>plan({root,path:hash+'.mp4',kind:'file'},'d'));
});
