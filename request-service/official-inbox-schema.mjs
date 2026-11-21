/** Transactional upgrade from known registration and cancellation ledgers. */
const columns='official_request_id,source_id,project_id,idempotency_key,request_type,schema_version,payload_json,payload_sha256,state,registered_at';
const stateChecks={
 registered:"state='registered'",
 cancel:"state IN ('registered','cancelled')",
 workflow:"state IN ('registered','planned','completed','failed','cancelled')",
};
const table=(name,version)=>`CREATE TABLE ${name}(
   official_request_id TEXT NOT NULL UNIQUE,
   source_id TEXT NOT NULL,
   project_id TEXT NOT NULL,
   idempotency_key TEXT NOT NULL,
   request_type TEXT NOT NULL,
   schema_version TEXT NOT NULL,
   payload_json TEXT NOT NULL,
   payload_sha256 TEXT NOT NULL,
   state TEXT NOT NULL CHECK(${stateChecks[version]}),
   registered_at INTEGER NOT NULL,
   PRIMARY KEY(source_id,project_id,idempotency_key)
  )`;
const indexSql=version=>[['id','npcId'],['slug','slug']].map(([name,field])=>`CREATE UNIQUE INDEX official_requests_new_npc_${name}
 ON official_requests(project_id,json_extract(payload_json,'$.character.${field}') COLLATE NOCASE)
  WHERE request_type='npc.workflow' AND ${version==='workflow'
  ?"schema_version IN ('ngs.npc-one-stop.v1','ngs.npc-one-stop.replace.v1') AND state IN ('registered','planned')"
  :"schema_version='ngs.npc-one-stop.v1'"+(version==='cancel'?" AND state='registered'":"")}`);
const norm=sql=>sql.replace(/"/g,'').replace(/\s+/g,'').trim();
const rejected=()=>Error('OFFICIAL_REQUEST_SCHEMA_UNSUPPORTED');
const objects=db=>db.prepare("SELECT type,sql FROM sqlite_schema WHERE tbl_name='official_requests' AND sql IS NOT NULL").all();
function equivalent(db,version){
 const rows=objects(db),expected=[table('official_requests',version),...indexSql(version)].map(norm).sort();
 const actual=rows.map(row=>norm(row.sql)).sort();
 return actual.length===expected.length&&actual.every((sql,i)=>sql===expected[i]);
}
export function initializeOfficialSchema(db){
 db.exec('PRAGMA busy_timeout=3000; PRAGMA synchronous=FULL; BEGIN IMMEDIATE');
 try{
  const old=db.prepare("SELECT 1 FROM sqlite_schema WHERE name='official_requests'").get();
  if(!old){
   db.exec(table('official_requests','workflow')+';'+indexSql('workflow').join(';'));
  }else if(equivalent(db,'registered')||equivalent(db,'cancel')){
   const foreign=db.prepare(`SELECT 1 FROM sqlite_schema AS obj JOIN pragma_foreign_key_list(obj.name) AS fk
    WHERE obj.type='table' AND fk."table"='official_requests' LIMIT 1`).get();
   if(foreign)throw rejected();
   const stage='official_requests_workflow_migration',fields='rowid,'+columns;
   db.exec(table(stage,'workflow'));
   db.exec(`INSERT INTO ${stage}(${fields}) SELECT ${fields} FROM official_requests`);
   const diff=(a,b)=>db.prepare(`SELECT 1 FROM (SELECT ${fields} FROM ${a} EXCEPT SELECT ${fields} FROM ${b}) LIMIT 1`).get();
   if(diff(stage,'official_requests')||diff('official_requests',stage))throw rejected();
   db.exec(`DROP TABLE official_requests; ALTER TABLE ${stage} RENAME TO official_requests;`+indexSql('workflow').join(';'));
  }
  if(!equivalent(db,'workflow'))throw rejected();
  db.exec('COMMIT');
 }catch(error){db.exec('ROLLBACK');throw error;}
}
