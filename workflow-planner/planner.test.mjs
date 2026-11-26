import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createServer} from 'node:http';
import {canonical,sha} from './envelope.mjs';
import {PlannerStore,pollOnce,directions,buildPlan} from './planner.mjs';
import {createRSClient,makeLoopbackTransport} from './rs.mjs';

function row(key='fixture',replace=true,combat=false){
 const payload={schemaVersion:replace?'ngs.npc-one-stop.replace.v1':'ngs.npc-one-stop.v1',projectId:'binari',
  character:{npcId:'npc.fixture',name:'테스트 인물',slug:'fixture',role:'마을 주민',actId:'act1',chapterId:'ch1'},
  intent:'불투명한 치마가 무릎을 가리는 동작',sourceRefs:['story.ch1.fixture'],includeCombat:combat};
 if(combat)payload.combatMotionPresets=['attack'];
 return {officialRequestId:'request.official.'+sha(key),projectId:'binari',requestType:'npc.workflow',schemaVersion:payload.schemaVersion,
  payload,payloadSha256:sha(canonical(payload)),status:'registered',registeredAt:1795290000000};
}
function fixture(t){
 const temp=mkdtempSync(join(tmpdir(),'workflow-planner-'));t.after(()=>rmSync(temp,{recursive:true,force:true}));
 const file=join(temp,'workflow.sqlite');
 return {file,open(){const store=new PlannerStore(file);t.after(()=>{if(store.db.isOpen)store.close();});return store;}};
}
const tasks=store=>store.db.prepare('SELECT * FROM workflow_tasks ORDER BY rowid').all();
const runs=store=>store.db.prepare('SELECT * FROM workflow_runs ORDER BY rowid').all();
const page=requests=>({requests,nextCursor:null});
const conf=()=>Object.assign(Error('fixture-conflict'),{rsStatus:409});
function service(rows,update=(id,status)=>({officialRequestId:id,status})){
 return {async call(tool,args){
  if(tool==='request_workflow_list')return page(rows);
  assert.equal(tool,'request_update_status');assert.equal(args.status,'planned');
  return update(args.officialRequestId,args.status);
 }};
}

test('exact Dajeong replacement produces 57 tasks, singular verified predecessor by role/direction and exact same NPC',async t=>{
 const f=fixture(t),s=f.open(),r=row();
 const rs=service([r],(id,status)=>{
  assert.equal(runs(s)[0].state,'awaiting_rs');assert.equal(tasks(s).length,57);return {officialRequestId:id,status};
 });
 assert.equal((await pollOnce(s,rs)).acknowledged,1);
 assert.equal((await pollOnce(s,rs)).staged,0);
 const rows=tasks(s),map=Object.fromEntries(rows.map(q=>[q.task_key,q]));
 assert.equal(rows.length,57);assert.equal(runs(s)[0].state,'planned');
 assert.deepEqual(rows.slice(0,8).map(q=>q.task_key),['delete','preset','planning_overview','planning_portrait_art','planning_representative','portrait_representative','planning_idle','portrait_idle_reference']);
 assert.deepEqual(rows.slice(0,8).map(q=>q.worker_type),['file','document','document','document','document','image','document','image']);
 assert.equal(map.delete.depends_on_task_id,null);
 assert.equal(map.preset.depends_on_task_id,map.delete.task_id);
 assert.equal(map.planning_overview.task_type,'npc.plan-overview');
 assert.equal(map.planning_portrait_art.task_type,'npc.plan-portrait-art');
 assert.equal(map.planning_overview.depends_on_task_id,map.preset.task_id);
 assert.equal(map.planning_portrait_art.depends_on_task_id,map.planning_overview.task_id);
 assert.equal(map.planning_representative.depends_on_task_id,map.planning_portrait_art.task_id);
 for(const k of ['planning_overview','planning_portrait_art']){
  assert.deepEqual(JSON.parse(map[k].task_input),{npcId:r.payload.character.npcId});
  assert.equal(map[k].task_id,'workflow.task.'+sha(canonical([r.officialRequestId,k])));
 }
 assert.equal(map.portrait_representative.depends_on_task_id,map.planning_representative.task_id);
 assert.equal(map.planning_idle.depends_on_task_id,map.planning_representative.task_id);
 assert.equal(map.portrait_idle_reference.depends_on_task_id,map.planning_idle.task_id);
 const middle=map.portrait_pixel_idle_reference;
 assert.equal(middle.depends_on_task_id,map.portrait_idle_reference.task_id);
 assert.equal(middle.worker_type,'postprocess');assert.equal(middle.task_type,'npc.portrait-pixel');
 assert.deepEqual(JSON.parse(middle.task_input),{npcId:r.payload.character.npcId,posePreset:'idle',direction:null});
 assert.equal(JSON.parse(map.portrait_idle_reference.task_input).imageStyle,'pixel-sprite.v1');
 assert.equal(Object.hasOwn(JSON.parse(map.portrait_representative.task_input),'imageStyle'),false);
 assert(!Object.keys(map).some(k=>k.includes('pixel_representative')));
 assert.deepEqual(JSON.parse(rows[0].task_input),{npcId:'npc.fixture',scope:'exact-npc-folder',preserveRequestHistory:true,noOldDataReuse:true});
 for(const d of directions){
  const portrait=map['portrait_'+d];assert.equal(portrait.depends_on_task_id,middle.task_id);
  assert.deepEqual(JSON.parse(portrait.task_input),{npcId:r.payload.character.npcId,direction:d,imageStyle:'pixel-sprite.v1'});
  const pixel=map['portrait_pixel_idle_'+d];assert.equal(pixel.depends_on_task_id,portrait.task_id);
  assert.equal(pixel.task_type,'npc.portrait-pixel');assert.equal(pixel.worker_type,'postprocess');
  assert.deepEqual(JSON.parse(pixel.task_input),{npcId:r.payload.character.npcId,posePreset:'idle',direction:d});
  for(const m of ['walk','run']){
   const a=map['animation_'+m+'_'+d],p=map['frames_'+m+'_'+d];
   assert.equal(a.depends_on_task_id,portrait.task_id);assert.equal(p.depends_on_task_id,a.task_id);
   assert.equal(a.worker_type,'animation');assert.equal(p.worker_type,'postprocess');
   assert.equal(JSON.parse(a.task_input).output,'native-png');assert.equal(JSON.parse(p.task_input).input,'native-png');
  }
 }
 assert(rows.every(t=>t.status==='pending'&&t.result_ref===null&&JSON.parse(t.task_input).npcId===r.payload.character.npcId));
});

test('new request has no delete, composite FK rejects cross-run dependencies, invalid and combat stay unplanned',async t=>{
 const s=fixture(t).open(), n=row('new',false),rep=row(),bad={...row('tampered'),payloadSha256:'a'.repeat(64)};
 const v=await pollOnce(s,service([n,rep,bad,row('combat',true,true)]));
 assert.equal(v.invalid,1);assert.equal(v.unsupported,1);assert.equal(v.staged,2);
 const a=tasks(s).filter(q=>q.official_request_id===n.officialRequestId),b=tasks(s).filter(q=>q.official_request_id===rep.officialRequestId);
 assert.equal(a.length,56);assert.equal(a[0].task_key,'preset');assert.equal(a[0].depends_on_task_id,null);
 assert.throws(()=>s.db.prepare('UPDATE workflow_tasks SET depends_on_task_id=? WHERE task_id=?').run(b[0].task_id,a[0].task_id),/FOREIGN KEY/);
 assert.throws(()=>s.stage({...n,registeredAt:0}),/WORKFLOW_REPLAY_CHANGED/);
});

test('transaction failure rolls back entire DAG before any RS status write',async t=>{
 const s=fixture(t).open();
 s.db.exec(`CREATE TRIGGER fail_last BEFORE INSERT ON workflow_tasks WHEN NEW.task_key='frames_run_nw' BEGIN SELECT RAISE(ABORT,'fixture'); END;`);
 const v=await pollOnce(s,service([row()],()=>assert.fail('no ACK on incomplete commit')));
 assert.equal(v.pollError,true);assert.equal(runs(s).length,0);assert.equal(tasks(s).length,0);
});

test('lost ACK stays awaiting; restart retries same exact status with empty registered feed',async t=>{
 const f=fixture(t);let s=f.open();const r=row();let planned=false;
 const v=await pollOnce(s,service([r],()=>{planned=true;throw Error('response lost');}));
 assert.equal(planned,true);assert.equal(v.pending,1);assert.equal(runs(s)[0].state,'awaiting_rs');
 s.close();s=f.open();
 const next=await pollOnce(s,service([],(id,status)=>{assert(planned);return {officialRequestId:id,status};}));
 assert.equal(next.acknowledged,1);assert.equal(runs(s)[0].state,'planned');assert.equal(tasks(s).length,57);
});

test('cancelled registration becomes not claimable, and malformed ACK never unlocks',async t=>{
 const s=fixture(t).open(), r=row();
 const v=await pollOnce(s,service([r],()=>({officialRequestId:'wrong',status:'planned'})));
 assert.equal(v.pending,1);assert.equal(runs(s)[0].state,'awaiting_rs');
 const next=await pollOnce(s,service([],()=>{throw conf();}));
 assert.equal(next.conflicts,1);assert.equal(runs(s)[0].state,'not_claimable');
 assert.equal((await pollOnce(s,service([r],()=>assert.fail('must not re-ack')))).pending,0);
});

test('actual local MCP HTTP exchange uses only list and update with id and cursor binding',async t=>{
 const s=fixture(t).open(),r=row(),calls=[];
 const srv=createServer(async(req,res)=>{
  let b='';for await(const part of req)b+=part;const m=JSON.parse(b);
  assert.equal(req.url,'/mcp');assert.equal(m.method,'tools/call');assert.equal(req.headers['mcp-protocol-version'],'2025-06-18');
  const {name,arguments:arg}=m.params;calls.push([name,arg]);
  const data=name==='request_workflow_list'?(arg.cursor?page([]):{requests:[r],nextCursor:'fixture-cursor'}):{...arg};
  res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify({jsonrpc:'2.0',id:m.id,result:{isError:false,structuredContent:data}}));
 });
 await new Promise(resolve=>srv.listen(0,'127.0.0.1',resolve));
 t.after(()=>new Promise(resolve=>srv.close(resolve)));
 const rs=createRSClient({transport:makeLoopbackTransport(srv.address().port)});
 assert.equal((await pollOnce(s,rs)).acknowledged,1);
 assert.deepEqual(calls,[['request_workflow_list',{limit:8}],['request_workflow_list',{limit:8,cursor:'fixture-cursor'}],
  ['request_update_status',{officialRequestId:r.officialRequestId,status:'planned'}]]);
 await assert.rejects(rs.call('request_cancel',{}),/TOOL_DENIED/);
});

test('only an all-complete run settles the official request through RS and lost ACK is safe',async t=>{
 const s=fixture(t).open(),r=row();
 await pollOnce(s,service([r]));
 let approvals=0;
 const rs={async call(tool,args){
  if(tool==='request_workflow_list')return page([]);
  assert.equal(tool,'request_update_status');
  assert.deepEqual(args,{officialRequestId:r.officialRequestId,status:'completed'});
  if(++approvals===1)throw Error('lost terminal ACK');
  return args;
 }};
 s.db.prepare("UPDATE workflow_tasks SET status='completed' WHERE task_key<>'frames_run_nw'").run();
 assert.equal((await pollOnce(s,rs)).completed,0);assert.equal(approvals,0);
 s.db.prepare("UPDATE workflow_tasks SET status='completed' WHERE task_key='frames_run_nw'").run();
 assert.equal((await pollOnce(s,rs)).pollError,true);
 assert.equal(runs(s)[0].reason,null);
 assert.equal((await pollOnce(s,rs)).completed,1);
 assert.equal(runs(s)[0].reason,'RS_COMPLETED');
 assert.equal((await pollOnce(s,rs)).completed,0);assert.equal(approvals,2);
});

test('retry count migrates existing tasks without replacing status, times or dependency, and is capped',async t=>{
 const f=fixture(t), s=f.open(), r=row();
 await pollOnce(s,service([r]));
 const id=tasks(s).find(x=>x.task_key==='animation_walk_s').task_id;
 s.db.prepare("UPDATE workflow_tasks SET status='failed',updated_at=123456789 WHERE task_id=?").run(id);
 const before=tasks(s).map(({retry_count,...x})=>x);
 s.db.exec('DROP TRIGGER workflow_dependency_insert; DROP TRIGGER workflow_dependency_update; DROP TRIGGER workflow_dependency_delete; ALTER TABLE workflow_tasks DROP COLUMN depends_on_task_id_5; ALTER TABLE workflow_tasks DROP COLUMN depends_on_task_id_4; ALTER TABLE workflow_tasks DROP COLUMN depends_on_task_id_3; ALTER TABLE workflow_tasks DROP COLUMN depends_on_task_id_2; ALTER TABLE workflow_tasks DROP COLUMN portrait_read_owner_pid; ALTER TABLE workflow_tasks DROP COLUMN retry_count; ALTER TABLE workflow_tasks DROP COLUMN retry_limit');
 s.close();
 const next=f.open();
 assert.deepEqual(tasks(next).map(({retry_count,...x})=>x),before);
 assert.ok(tasks(next).every(x=>x.retry_count===0));
 assert.throws(()=>next.db.prepare('UPDATE workflow_tasks SET retry_count=? WHERE task_id=?').run(4,id),/CHECK constraint/);
 next.db.prepare('UPDATE workflow_tasks SET retry_count=3 WHERE task_id=?').run(id);
 next.close();
 assert.equal(tasks(f.open()).find(x=>x.task_id===id).retry_count,3);
});

function prePixelFixture(s,r){
 s.stage(r);
 const old=buildPlan(r,{pixelPortraits:false});
 const update=s.db.prepare('UPDATE workflow_tasks SET task_input=?,depends_on_task_id=? WHERE task_id=?');
 for(const task of old.tasks)update.run(canonical(task.input),task.dependsOn,task.id);
 s.db.prepare("DELETE FROM workflow_tasks WHERE task_type='npc.portrait-pixel'").run();
}

test('replay of existing 48-task original-picture DAG does not add pixel tasks or change dependencies',async t=>{
 const f=fixture(t),s=f.open(),r=row('existing-active');
 prePixelFixture(s,r);
 const before=tasks(s);
 assert.equal(before.length,48);
 assert.equal(before.some(q=>q.task_type==='npc.portrait-pixel'),false);
 assert.equal(before.find(q=>q.task_key==='portrait_s').depends_on_task_id,before.find(q=>q.task_key==='portrait_idle_reference').task_id);
 s.close();
 const resumed=f.open();
 assert.equal((await pollOnce(resumed,service([r]))).staged,0);
 assert.deepEqual(tasks(resumed),before);
});

test('registered replay after restart preserves historical 46-task DAG, progress and terminal settlement',async t=>{
 const f=fixture(t),s=f.open(),r=row('historical');
 prePixelFixture(s,r);
 const oldShape=s.db.prepare('SELECT task_id FROM workflow_tasks WHERE task_key=?');
 s.db.prepare("UPDATE workflow_tasks SET depends_on_task_id=? WHERE task_key='planning_representative'").run(oldShape.get('preset').task_id);
 for(const key of ['planning_portrait_art','planning_overview']){
  s.db.prepare('DELETE FROM workflow_tasks WHERE task_key=?').run(key);
 }
 s.db.prepare("UPDATE workflow_tasks SET status='completed',updated_at=123,result_ref='preserved-ref',retry_count=1 WHERE task_key='portrait_representative'").run();
 const before=tasks(s);
 assert.equal(before.length,46);
 s.close();
 const resumed=f.open();
 const v=await pollOnce(resumed,service([r]));
 assert.equal(v.staged,0);assert.equal(v.invalid,0);assert.equal(v.acknowledged,1);
 assert.deepEqual(tasks(resumed),before);
 resumed.db.exec("UPDATE workflow_tasks SET status='completed'");
 const calls=[];
 const rs={async call(tool,args){if(tool==='request_workflow_list')return page([r]);calls.push(args);return args;}};
 assert.equal((await pollOnce(resumed,rs)).completed,1);
 assert.equal((await pollOnce(resumed,rs)).completed,0);
 assert.deepEqual(calls,[{officialRequestId:r.officialRequestId,status:'completed'}]);
 assert.equal(tasks(resumed).length,46);
});

function conceptRow(mode,override={}){
 const payload={schemaVersion:'ngs.concept-category-workflow.v1',projectId:'binari',operation:mode,
  categoryId:mode==='add'?'category.add.'+'d'.repeat(32):'character',groupId:mode==='add'?'group.sound':null,
  title:'표현 기획',outline:['add','replace'].includes(mode)?'전체 문서 구성':'',requirements:mode==='update'?'내용 수정':'',...override};
 return {officialRequestId:'request.official.'+sha('concept-'+mode),projectId:'binari',requestType:'concept.category.workflow',
  schemaVersion:payload.schemaVersion,payload,payloadSha256:sha(canonical(payload)),status:'registered',registeredAt:1795290000000};
}
test('concept planner creates only exact add/update/migrate/remove task graphs and preserves existing NPC replay',async t=>{
 const store=fixture(t).open(),npc=row('prior-npc');
 store.stage(npc);
 const old=tasks(store);
 const graph={add:[['category_generate','concept.category-generate','document',null]],
  update:[['category_generate','concept.category-generate','document',null]],
  migrate:[['category_generate','concept.category-generate','document',null]],
  remove:[['category_delete','concept.category-delete','file',null]]};
 const concept=Object.keys(graph).map(mode=>conceptRow(mode));
 const result=await pollOnce(store,service([npc,...concept]));
 assert.equal(result.staged,4);assert.equal(result.acknowledged,5);
 for(const item of concept){
  const same=tasks(store).filter(x=>x.official_request_id===item.officialRequestId);
  assert.deepEqual(same.map(x=>[x.task_key,x.task_type,x.worker_type,same.find(d=>d.task_id===x.depends_on_task_id)?.task_key??null]),graph[item.payload.operation]);
  assert(same.every(x=>x.task_input===canonical({categoryId:item.payload.categoryId})&&x.status==='pending'));
  assert.equal(store.stage(item),false);
 }
 assert.deepEqual(tasks(store).filter(x=>x.official_request_id===npc.officialRequestId),old);
});

test('invalid concept scope increments invalid count without blocking ordinary NPC staging',async t=>{
 const store=fixture(t).open();
 const malformed=[conceptRow('add',{groupId:null}),conceptRow('migrate',{outline:'skip formal conversion'}),conceptRow('remove',{categoryId:'../all'})];
 const result=await pollOnce(store,service([...malformed,row('npc')]));
 assert.equal(result.invalid,3);assert.equal(result.staged,1);assert.equal(result.pollError,false);
 assert.equal(runs(store).length,1);
});

test('new planner refuses a destructive legacy admission; existing persisted workflow replays unchanged',async t=>{
 const s=fixture(t).open(),row=conceptRow('replace');
 const result=await pollOnce(s,service([row],()=>assert.fail('no status ACK for unstaged destructive replace')));
 assert.equal(result.staged,0);assert.equal(result.invalid,1);assert.equal(tasks(s).length,0);
 const plan=buildPlan(row);
 s.db.prepare("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,?,'awaiting_rs',?,?)").run(row.officialRequestId,row.projectId,row.requestType,
  row.schemaVersion,plan.payload,row.payloadSha256,row.registeredAt,2,null);
 for(const q of plan.tasks)s.db.prepare('INSERT INTO workflow_tasks(task_id,official_request_id,task_key,task_type,worker_type,task_input,depends_on_task_id,updated_at) VALUES(?,?,?,?,?,?,?,?)').run(q.id,row.officialRequestId,q.key,q.type,q.workerType,canonical(q.input),q.dependsOn,3);
 const persisted=tasks(s);
 const replay=await pollOnce(s,service([row]));
 assert.equal(replay.staged,0);assert.equal(replay.invalid,0);assert.equal(replay.acknowledged,1);
 assert.deepEqual(tasks(s),persisted);
});

test('fresh add section list is immutable intake data and creates only one document task',t=>{
 const s=fixture(t).open(),r=conceptRow('add',{requiredSections:['범위','규칙']});
 assert.equal(s.stage(r),true);
 assert.equal(tasks(s).length,1);
 assert.equal(tasks(s)[0].task_type,'concept.category-generate');
 assert.deepEqual(JSON.parse(runs(s)[0].payload).requiredSections,['범위','규칙']);
 const changed={...r.payload,requiredSections:['규칙','범위']};
 assert.throws(()=>s.stage({...r,payload:changed,payloadSha256:sha(canonical(changed))}),/WORKFLOW_REPLAY_CHANGED/);
});

test('new document and image have 30 retries; other lanes remain capped at three',async t=>{
 const {open}=fixture(t),s=open();
 await pollOnce(s,service([row()]));
 const rows=tasks(s);
 for(const role of ['document','image','animation','postprocess']){
  const task=rows.find(x=>x.worker_type===role);
  assert.ok(task);
  const cap=['document','image'].includes(role)?30:3;
  assert.equal(task.retry_limit,cap);
  s.db.prepare('UPDATE workflow_tasks SET retry_count=? WHERE task_id=?').run(cap,task.task_id);
  assert.throws(()=>s.db.prepare('UPDATE workflow_tasks SET retry_count=? WHERE task_id=?').run(cap+1,task.task_id),/CHECK constraint/);
 }
});
