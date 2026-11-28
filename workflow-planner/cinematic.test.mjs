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

test('PNG sequence graph and completion prove exact authored numeric curves without a postprocess stage',async()=>{
 const {cinematicSequenceType,cinematicSequenceSchema,cinematicSequenceResultRef}=await import('./cinematic-envelope.mjs');
 const playback={durationFrames:132,speedCurve:[{frame:0,rate:1/3},{frame:120,rate:0.0000001}],
  cameraCurve:[{frame:0,x:.5,y:.5,zoom:1},{frame:120,x:.6,y:.4,zoom:2}]};
 const payload={...p,schemaVersion:cinematicSequenceSchema,playback};
 const input={...request(payload),requestType:cinematicSequenceType,schemaVersion:cinematicSequenceSchema};
 const plan=buildPlan(input);
 assert.deepEqual(plan.tasks.map(t=>[t.key,t.type,t.workerType,t.dependsOn]),
  [['cinematic_sequence_render','cinematic.sequence.generate.render','animation',null]]);
 assert.deepEqual(plan.tasks[0].input,{sourceSha256,motionPrompt:p.motionPrompt,playback});
 const manifest={schemaVersion:'ngs.cinematic-sequence-result.v1',projectId:'binari',officialRequestId:oid,sourceSha256,
  width:512,height:352,masterFps:24,frameCount:45,frames:Array.from({length:45},(_,i)=>({index:i,sha256:'f'.repeat(64),bytes:1024})),playback};
 const resultRef=cinematicSequenceResultRef(oid);
 let proof=null;const store=new PlannerStore(':memory:',{cinematicEnabled:true,readCinematicPublication:async()=>proof});
 try{
  store.stage(input);store.acknowledge(oid);
  store.db.prepare("UPDATE workflow_tasks SET status='completed',result_ref=?").run(resultRef);
  const evidence=store.terminalReady()[0];
  assert.equal(evidence.publication.family,'cinematic-sequence');
  assert.equal(await store.verifyCompletion(evidence),false);
  proof={ok:true,resultRef,manifest:{...manifest,playback:{...playback,durationFrames:140}}};
  assert.equal(await store.verifyCompletion(evidence),false);
  proof={ok:true,resultRef,manifest:{...manifest,frames:manifest.frames.slice(1)}};
  assert.equal(await store.verifyCompletion(evidence),false);
  proof={ok:true,resultRef,manifest};
  assert.equal(await store.verifyCompletion(evidence),true);
 }finally{store.close();}
});

test('formal pixel background has three exact dependencies and two image calls',async()=>{
 const {buildPlan}=await import('./planner.mjs');
 const {validateExtraCinematic,backgroundPixelType,backgroundPixelSchema}=await import('./cinematic-envelope.mjs');
 const payload={schemaVersion:backgroundPixelSchema,projectId:'binari',title:'어두운 앞마당',
  timeOfDay:'밤',season:'늦가을',mood:'고요함',color:'남청',location:'한옥',
  requirements:'동선을 읽을 수 있게',additionalNotes:''};
 const payloadSha256=sha(validateExtraCinematic(payload,'binari',backgroundPixelType));
 const officialRequestId='request.official.'+'a'.repeat(64);
 const row={officialRequestId,projectId:'binari',requestType:backgroundPixelType,schemaVersion:backgroundPixelSchema,
  payload,payloadSha256,registeredAt:1,status:'registered'};
 const plan=buildPlan(row);
 assert.deepEqual(plan.tasks.map(x=>[x.key,x.workerType]),[
  ['background_cinematic_source','image'],['background_pixel_generate','image'],['background_pixel_postprocess','postprocess']]);
 assert.deepEqual(plan.tasks.map(x=>x.dependsOn),[null,plan.tasks[0].id,plan.tasks[1].id]);
 assert.throws(()=>validateExtraCinematic({...payload,width:384},'binari',backgroundPixelType));
});

test('background requests choose one or two independent tasks and require both exact publication receipts',async()=>{
 const {backgroundAnimationType:type,backgroundAnimationSchema:schema,validateExtraCinematic,cinematicSequenceResultRef}=await import('./cinematic-envelope.mjs');
 const playback={durationFrames:132,speedCurve:[{frame:0,rate:1/3}],cameraCurve:[{frame:0,x:.5,y:.5,zoom:1}]};
 const pixel='c'.repeat(64);
 const payload={schemaVersion:schema,projectId:'binari',backgroundSha256:pixel,references:{cinematic:sourceSha256,pixel},
  motionPrompt:p.motionPrompt,playback};
 const asRow=payload=>({...request(payload),requestType:type,schemaVersion:schema,payloadSha256:sha(validateExtraCinematic(payload,'binari',type))});
 const row=asRow(payload),plan=buildPlan(row);
 assert.deepEqual(plan.tasks.map(t=>[t.workerType,t.dependsOn,t.input.variant,t.input.sourceSha256,t.input.motionPrompt]),
  [['animation',null,'cinematic',sourceSha256,p.motionPrompt],['animation',null,'pixel',pixel,p.motionPrompt]]);
 assert.equal(buildPlan(asRow({...payload,references:{pixel}})).tasks.length,1);
 for(const references of [{},{draft:pixel},{cinematic:pixel,pixel}])assert.throws(()=>asRow({...payload,references}),/INVALID/);
 let broken='pixel';
 const store=new PlannerStore(':memory:',{cinematicEnabled:true,readCinematicPublication:async out=>({ok:true,resultRef:out.resultRef,manifest:{
  schemaVersion:'ngs.cinematic-sequence-result.v1',projectId:'binari',officialRequestId:out.officialRequestId,
  sourceSha256:out.group.variant===broken?'d'.repeat(64):out.sourceSha256,group:out.group,playback,width:1024,height:704,
  upscale:{model:'seedvr2-7b-fp16',scale:2,sourceWidth:512,sourceHeight:352,...(out.group.variant==='pixel'?{pixelCorrection:{method:'reference-palette-v1',upscaledFramesSha256:'a'.repeat(64)}}:{})},
  masterFps:24,frameCount:45,frames:Array.from({length:45},(_,i)=>({index:i,bytes:1024,sha256:'e'.repeat(64)}))
 }})});
 try{
  store.stage(row);store.acknowledge(oid);
  const update=store.db.prepare("UPDATE workflow_tasks SET status='completed',result_ref=? WHERE task_id=?");
  const finish=t=>update.run(cinematicSequenceResultRef('request.official.'+sha(canonical([oid,t.key]))),t.id);
  finish(plan.tasks[0]);
  assert.deepEqual(store.terminalReady(),[]);
  store.db.prepare("UPDATE workflow_tasks SET status='failed',retry_count=retry_limit WHERE task_id=?").run(plan.tasks[1].id);
  assert.deepEqual(store.terminalFailures(),[{official_request_id:oid,request_type:type}]);
  finish(plan.tasks[1]);
  const [candidate]=store.terminalReady();
  assert.equal(candidate.publication.outputs.length,2);
  assert.equal(await store.verifyCompletion(candidate),false);
  broken=null;
  assert.equal(await store.verifyCompletion(candidate),true);
 }finally{store.close();}
});

test('staged background is a graph of shared operations: independent lanes with real predecessor IDs',async()=>{
 const {backgroundAnimationType:type,backgroundAnimationStageSchema:schema,validateExtraCinematic}=await import('./cinematic-envelope.mjs');
 const playback={durationFrames:264,speedCurve:[{frame:0,rate:1/3}],cameraCurve:[{frame:0,x:.5,y:.5,zoom:1}]};
 const pixel='a'.repeat(64);
 const payload={schemaVersion:schema,projectId:'binari',backgroundSha256:pixel,references:{cinematic:sourceSha256,pixel},
  motionPrompt:p.motionPrompt,playback};
 const row={...request(payload),requestType:type,schemaVersion:schema,payloadSha256:sha(validateExtraCinematic(payload,'binari',type))};
 const {tasks}=buildPlan(row);
 assert.equal(tasks.length,7);
 for(const [variant,expected] of [['cinematic',['media.animation.render','media.frames.upscale','media.sequence.publish']],
  ['pixel',['media.animation.render','media.frames.upscale','media.frames.pixel-correct','media.sequence.publish']]]){
  const lane=tasks.filter(t=>t.input.variant===variant);
  assert.deepEqual(lane.map(t=>t.type),expected);
  assert.deepEqual(lane.map(t=>t.dependsOn),[null,...lane.slice(0,-1).map(t=>t.id)]);
 }
 const store=new PlannerStore(':memory:',{cinematicEnabled:true});
 try{
  store.stage(row);store.acknowledge(oid);
  const update=store.db.prepare("UPDATE workflow_tasks SET status='completed',result_ref=? WHERE task_id=?");
  for(const t of tasks){
   const id='request.official.'+sha(canonical([oid,'background_animation_'+t.input.variant]));
   update.run(t.input.stage==='publish'?'binari/cinematics/sequences/'+id+'/manifest.json':'background.transient.'+'e'.repeat(64),t.id);
  }
  const [done]=store.terminalReady();assert.equal(done.publication.outputs.length,2);
  store.db.prepare("UPDATE workflow_tasks SET status='failed',retry_count=retry_limit,result_ref=NULL WHERE task_key='background_animation_pixel_upscale'").run();
  store.db.prepare("UPDATE workflow_tasks SET status='pending',result_ref=NULL WHERE task_key IN ('background_animation_pixel_pixel_correct','background_animation_pixel_publish')").run();
  assert.equal(store.terminalReady().length,0);
  assert.deepEqual(store.terminalFailures(),[{official_request_id:oid,request_type:type}]);
 }finally{store.close();}
});

test('cinematic still readback supports 2:1 and legacy; background remains 3:2',async()=>{
 const {verifyExtraCinematicReadback}=await import('./cinematic-readback.mjs');
 for(const background of [false,true]){
  const prefix=background?'background':'cinematic';
  const resultRef='binari/cinematics/'+(background?'backgrounds':'sources')+'/'+sourceSha256+'.json';
  const candidate={family:prefix+'.source.generate',officialRequestId:oid,title:'Wide source',resultRef};
  const manifest={schemaVersion:'ngs.'+prefix+'-source-result.v1',projectId:'binari',officialRequestId:oid,
   title:candidate.title,sha256:sourceSha256,bytes:12000,width:1536,height:1024};
  assert.equal(verifyExtraCinematicReadback(candidate,{ok:true,resultRef,manifest}),true);
  assert.equal(verifyExtraCinematicReadback(candidate,{ok:true,resultRef,manifest:{...manifest,height:768}}),!background);
 }
});
