import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {join} from 'node:path';
import {mkdtempSync,realpathSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {PlannerStore} from './planner.mjs';
import {createWorkflowTasksSql,migrateWorkflowAudioTask} from './audio-task-schema.mjs';
import {canonical,sha} from './envelope.mjs';

const runs=`CREATE TABLE workflow_runs(
 official_request_id TEXT PRIMARY KEY NOT NULL, project_id TEXT NOT NULL,
 request_type TEXT NOT NULL, schema_version TEXT NOT NULL, payload TEXT NOT NULL,
 payload_sha256 TEXT NOT NULL, registered_at INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('awaiting_rs','planned','failed','not_claimable')),
 updated_at INTEGER NOT NULL, reason TEXT
) STRICT;`;
const old=createWorkflowTasksSql.replace(",'audio'",'');
const fixture=(t,mod=x=>x)=>{
 const dir=realpathSync(mkdtempSync(join(tmpdir(),'workflow-audio-migration-')));
 t.after(()=>rmSync(dir,{recursive:true,force:true}));
 const path=join(dir,'old.sqlite'),db=new DatabaseSync(path);
 db.exec(`PRAGMA foreign_keys=ON; ${runs}${mod(old)};`);
 db.exec(`INSERT INTO workflow_runs VALUES('base','binari','npc.workflow','old','{}','old-hash',1,'planned',2,NULL);
 INSERT INTO workflow_tasks(rowid,task_id,official_request_id,task_key,task_type,worker_type,task_input,result_ref,
   updated_at,depends_on_task_id,status,retry_count) VALUES
   (9,'a','base','delete','npc.delete-folder','file','{}','old-receipt',7,NULL,'completed',2),
   (26,'b','base','preset','npc.preset','document','{}',NULL,17,'a','running',0),
   (41,'c','base','animation','npc.animation-native-png','animation','{}',NULL,12,'b','failed',3);`);
 return {db,path};
};
const rows=db=>db.prepare('SELECT rowid,* FROM workflow_tasks ORDER BY rowid').all();
const runRows=db=>db.prepare('SELECT rowid,* FROM workflow_runs ORDER BY rowid').all();

test('legacy audio worker CHECK upgrade preserves exact historical rows, statuses, retry and self FK across restart',t=>{
 const {db,path}=fixture(t),before=rows(db),parents=runRows(db);
 db.close();
 const upgraded=new PlannerStore(path);
 assert.deepEqual(rows(upgraded.db).map(({portrait_read_owner_pid,retry_limit,...r})=>r),before.map(r=>({...r})));
 assert.ok(rows(upgraded.db).every(r=>r.portrait_read_owner_pid===null));assert.deepEqual(runRows(upgraded.db),parents);
 assert.deepEqual(upgraded.db.prepare('PRAGMA foreign_key_check').all(),[]);
 assert.throws(()=>upgraded.db.exec("UPDATE workflow_tasks SET depends_on_task_id='missing' WHERE task_id='b'"),/FOREIGN KEY/);
 assert.throws(()=>upgraded.db.exec("UPDATE workflow_tasks SET worker_type='unknown' WHERE task_id='b'"),/CHECK/);
 assert.equal(migrateWorkflowAudioTask(upgraded.db),false);
 const payload={schemaVersion:'ngs.audio-generate.v1',projectId:'binari',kind:'bgm',title:'fixture',
  conceptId:'sound',brief:'fixture brief',text:'',durationSeconds:20};
 assert(upgraded.stage({officialRequestId:'request.official.'+sha('audio'),projectId:'binari',requestType:'audio.generate',
  schemaVersion:payload.schemaVersion,payload,payloadSha256:sha(canonical(payload)),status:'registered',registeredAt:1}));
 assert.equal(rows(upgraded.db).at(-1).worker_type,'audio');upgraded.close();
 const resumed=new PlannerStore(path);
 t.after(()=>resumed.close());
 assert.deepEqual(rows(resumed.db).slice(0,3).map(({portrait_read_owner_pid,retry_limit,...r})=>r),before.map(r=>({...r})));
 assert.ok(rows(resumed.db).every(r=>r.portrait_read_owner_pid===null));
 assert.equal(rows(resumed.db).at(-1).worker_type,'audio');
});

test('unknown worker check or unexpected attached schema fails transaction without copying rows',t=>{
 for(const mod of [s=>s.replace("'postprocess'","'postprocess','runtime-unknown'"),s=>s]){
  const {db}=fixture(t,mod);t.after(()=>db.close());
  if(mod(old)===old)db.exec('CREATE INDEX unrelated_index ON workflow_tasks(status)');
  const before=rows(db),schema=db.prepare('SELECT * FROM sqlite_schema').all();
  assert.throws(()=>migrateWorkflowAudioTask(db),/WORKFLOW_AUDIO_TASK_SCHEMA_UNKNOWN/);
  assert.deepEqual(rows(db),before);assert.deepEqual(db.prepare('SELECT * FROM sqlite_schema').all(),schema);
 }
});
