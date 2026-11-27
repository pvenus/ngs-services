/** Narrow lossless extension of the original workflow task worker CHECK. */
export const WORKFLOW_TASK_MAX_RETRIES=3;
export const RETRY_COUNT_COLUMN=`retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count BETWEEN 0 AND ${WORKFLOW_TASK_MAX_RETRIES})`;
export const FAST_RETRY_LIMIT=30;
// A pinned limit preserves already-exhausted jobs and non-Codex worker policy.
const retryLimit="retry_limit INTEGER NOT NULL DEFAULT 3 CHECK (retry_limit IN (3,30) AND (retry_limit=3 OR worker_type IN ('image','document')))";
const policyCount="retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count BETWEEN 0 AND retry_limit)";
export const PORTRAIT_READ_OWNER_COLUMN='portrait_read_owner_pid INTEGER CHECK (portrait_read_owner_pid > 0)';
const live='workflow_tasks',stage='workflow_tasks_audio_stage';
const oldWorkers="'file','document','image','animation','postprocess'";
const columns='task_id,official_request_id,task_key,task_type,worker_type,task_input,result_ref,updated_at,depends_on_task_id,status,retry_count';
function ddl(table,{audio=true,retry=true,ifMissing=false,portraitReadOwner=false,policy=false}={}){
 return `CREATE TABLE ${ifMissing?'IF NOT EXISTS ':''}${table}(
    task_id TEXT PRIMARY KEY NOT NULL, official_request_id TEXT NOT NULL,
    task_key TEXT NOT NULL, task_type TEXT NOT NULL,
    worker_type TEXT NOT NULL CHECK(worker_type IN (${oldWorkers}${audio?",'audio'":''})),
    task_input TEXT NOT NULL, result_ref TEXT, updated_at INTEGER NOT NULL,
    depends_on_task_id TEXT, status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','completed','failed')),
    ${retry?(policy?policyCount:RETRY_COUNT_COLUMN)+',':''}
    ${policy?retryLimit+',':''}
    ${portraitReadOwner?PORTRAIT_READ_OWNER_COLUMN+',':''}
    UNIQUE(official_request_id,task_key), UNIQUE(official_request_id,task_id),
    CHECK(depends_on_task_id IS NULL OR depends_on_task_id<>task_id),
    FOREIGN KEY (official_request_id) REFERENCES workflow_runs(official_request_id),
    FOREIGN KEY (official_request_id,depends_on_task_id) REFERENCES ${table}(official_request_id,task_id)
  ) STRICT`;
}
export const createWorkflowTasksSql=ddl(live,{ifMissing:true});
const normal=sql=>sql?.replace(/"workflow_tasks"/g,live).replace(/'[^']*'|\s+/g,token=>token[0]==="'"?token:'');
const legacy=normal(ddl(live,{audio:false})),current=normal(ddl(live)),withPortraitReadOwner=normal(ddl(live,{portraitReadOwner:true}));
const withRetryPolicy=normal(ddl(live,{portraitReadOwner:true,policy:true}));
export const ADDITIONAL_DEPENDENCY_COLUMNS=Object.freeze([2,3,4,5].map(n=>'depends_on_task_id_'+n));
const dependencyColumns=ADDITIONAL_DEPENDENCY_COLUMNS.map((col,index)=>{
 const parents=['depends_on_task_id',...ADDITIONAL_DEPENDENCY_COLUMNS.slice(0,index)];
 return `${col} TEXT CHECK (${col} IS NULL OR (
  length(${col})=78 AND substr(${col},1,14)='workflow.task.' AND substr(${col},15) NOT GLOB '*[^0-9a-f]*'
  AND ${col}<>task_id AND ${parents.map(p=>p+' IS NOT NULL AND '+col+'<>'+p).join(' AND ')}))`;
});
const withDependencies=prefix=>withRetryPolicy.replace('UNIQUE(official_request_id,task_key)',
 prefix.map(col=>normal(col)+',').join('')+'UNIQUE(official_request_id,task_key)');
const dependencySchemas=dependencyColumns.map((_,i)=>withDependencies(dependencyColumns.slice(0,i+1)));
const mapPartColumn='map_part_json TEXT';
const withMapPart=dependencySchemas.at(-1).replace('UNIQUE(official_request_id,task_key)',
 normal(mapPartColumn)+',UNIQUE(official_request_id,task_key)');
const failure=()=>Error('WORKFLOW_AUDIO_TASK_SCHEMA_UNKNOWN');

export function migrateWorkflowDependencies(db){
 db.exec('BEGIN IMMEDIATE');
 try{
  const sql=normal(db.prepare("SELECT sql FROM sqlite_schema WHERE type='table' AND name=?").get(live)?.sql);
  const index=sql===withMapPart?dependencyColumns.length-1:dependencySchemas.indexOf(sql);
  if(index<0&&sql!==withRetryPolicy)throw Error('WORKFLOW_DEPENDENCY_SCHEMA_UNKNOWN');
  for(const column of dependencyColumns.slice(index+1))db.exec(`ALTER TABLE ${live} ADD COLUMN ${column}`);
  if(sql!==withMapPart)db.exec(`ALTER TABLE ${live} ADD COLUMN ${mapPartColumn}`);
  // SQLite ADD COLUMN cannot attach a compound (request, parent) foreign key.
  // Explicit guards preserve its same-request and target-existence guarantees
  // without replacing the live table or adding another table.
  const parentGuard=ADDITIONAL_DEPENDENCY_COLUMNS.map(c=>
   `NEW.${c} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM ${live}
    WHERE official_request_id=NEW.official_request_id AND task_id=NEW.${c})`).join(' OR ');
  const referenced=`EXISTS (SELECT 1 FROM ${live} WHERE official_request_id=OLD.official_request_id
   AND OLD.task_id IN (${ADDITIONAL_DEPENDENCY_COLUMNS.join(',')}))`;
  const abort=`SELECT RAISE(ABORT,'WORKFLOW_DEPENDENCY_FOREIGN_KEY');`;
  const parentFields=['task_id','official_request_id'];
  db.exec(`CREATE TRIGGER IF NOT EXISTS workflow_dependency_insert BEFORE INSERT ON ${live}
    WHEN ${parentGuard} BEGIN ${abort} END;
   CREATE TRIGGER IF NOT EXISTS workflow_dependency_update
    BEFORE UPDATE OF ${[...parentFields,...ADDITIONAL_DEPENDENCY_COLUMNS].join(',')} ON ${live}
    WHEN (${parentGuard}) OR ((OLD.task_id<>NEW.task_id OR OLD.official_request_id<>NEW.official_request_id)
      AND ${referenced}) BEGIN ${abort} END;
   CREATE TRIGGER IF NOT EXISTS workflow_dependency_delete BEFORE DELETE ON ${live}
    WHEN ${referenced} BEGIN ${abort} END;`);
  db.exec('COMMIT');
 }catch(error){db.exec('ROLLBACK');throw error;}
}


export function migrateWorkflowAudioTask(db){
 db.exec('BEGIN IMMEDIATE');
 try{
  const sql=db.prepare("SELECT sql FROM sqlite_schema WHERE type='table' AND name=?").get(live)?.sql;
  if(normal(sql)===current||normal(sql)===withPortraitReadOwner||normal(sql)===withRetryPolicy||dependencySchemas.includes(normal(sql))||normal(sql)===withMapPart){db.exec('COMMIT');return false;}
  if(normal(sql)!==legacy||
     db.prepare("SELECT count(*) AS n FROM sqlite_schema WHERE (type IN ('index','trigger') AND tbl_name=? AND sql IS NOT NULL) OR type='view'").get(live).n!==0||
     db.prepare(`PRAGMA foreign_key_check(${live})`).all().length)throw failure();
  // Every task retains its explicit rowid as well as its own state and IDs.
  // Defer checks until all self dependencies are copied and the old table is gone.
  db.exec(`PRAGMA defer_foreign_keys=ON; ${ddl(stage)};
    INSERT INTO ${stage}(rowid,${columns}) SELECT rowid,${columns} FROM ${live};`);
  for(const [a,b] of [[live,stage],[stage,live]]){
   if(db.prepare(`SELECT rowid,${columns} FROM ${a} EXCEPT SELECT rowid,${columns} FROM ${b} LIMIT 1`).get())
    throw failure();
  }
  if(db.prepare(`PRAGMA foreign_key_check(${stage})`).all().length)throw failure();
  db.exec(`DROP TABLE ${live}; ALTER TABLE ${stage} RENAME TO ${live};`);
  if(db.prepare('PRAGMA foreign_key_check').all().length)throw failure();
  db.exec('COMMIT');return true;
 }catch(error){db.exec('ROLLBACK');throw error;}
}


export function migrateWorkflowRetryPolicy(db) {
 const policyStage='workflow_tasks_retry_stage';
 const copied=columns+',portrait_read_owner_pid';
 db.exec('BEGIN IMMEDIATE');
 try {
  const sql=normal(db.prepare("SELECT sql FROM sqlite_schema WHERE type='table' AND name=?").get(live)?.sql);
  if(sql===withRetryPolicy||dependencySchemas.includes(sql)||sql===withMapPart){db.exec('COMMIT');return false;}
  if(sql!==withPortraitReadOwner||
     db.prepare("SELECT count(*) AS n FROM sqlite_schema WHERE (type IN ('index','trigger') AND tbl_name=? AND sql IS NOT NULL) OR type='view'").get(live).n||
     db.prepare(`PRAGMA foreign_key_check(${live})`).all().length)throw Error('WORKFLOW_RETRY_POLICY_SCHEMA_UNKNOWN');
  db.exec(`PRAGMA defer_foreign_keys=ON; ${ddl(policyStage,{portraitReadOwner:true,policy:true})};
   INSERT INTO ${policyStage}(rowid,${copied},retry_limit)
   SELECT t.rowid,${copied.split(',').map(x=>'t.'+x).join(',')},
    CASE WHEN t.worker_type IN ('image','document') AND t.status IN ('pending','running','failed')
          AND (t.status<>'failed' OR t.retry_count<3) AND r.state IN ('planned','awaiting_rs')
          AND r.reason IS NULL THEN 30 ELSE 3 END
   FROM ${live} t JOIN workflow_runs r USING(official_request_id);`);
  for(const [a,b] of [[live,policyStage],[policyStage,live]])
   if(db.prepare(`SELECT rowid,${copied} FROM ${a} EXCEPT SELECT rowid,${copied} FROM ${b} LIMIT 1`).get())
    throw Error('WORKFLOW_RETRY_POLICY_MIGRATION_MISMATCH');
  db.exec(`DROP TABLE ${live}; ALTER TABLE ${policyStage} RENAME TO ${live};`);
  if(db.prepare('PRAGMA foreign_key_check').all().length)throw Error('WORKFLOW_RETRY_POLICY_FOREIGN_KEY');
  db.exec('COMMIT');return true;
 }catch(err){db.exec('ROLLBACK');throw err;}
}
