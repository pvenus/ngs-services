/** Settle only authoritative, never-planned registration rows unsupported by this release. */
import {canonical,ident,sha} from './one-stop-envelope.mjs';
import {canonicalWorkflowPayload,knownWorkflowSchema} from './request-envelope.mjs';
import {officialIdFor,spaceQueueIdPattern} from './space-intake.mjs';

export const unsupportedReason='REQUEST_UNSUPPORTED_RECREATE';
const family=/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const spaceKey=new RegExp(spaceQueueIdPattern);
const maximumPayloadBytes=64*1024;

/** Current active Planner disallows these otherwise valid historic variants. */
export function unsupportedPlannerPolicy(type,payload){
 return type==='npc.workflow'&&payload.includeCombat===true||
  type==='concept.category.workflow'&&payload.operation==='replace';
}

function unsupported(row){
 const {officialRequestId:officialId,sourceId,projectId,sourceKey:idempotencyKey,
  requestType:type,schemaVersion:schema,payloadJson:raw,payloadSha256:hash,registeredAt,status}=row;
 if(status!=='registered'||projectId!=='binari'||!['local','space'].includes(sourceId)||!ident(idempotencyKey)||
    sourceId==='space'&&!spaceKey.test(idempotencyKey)||officialId!==officialIdFor({sourceId,projectId,idempotencyKey})||
    !Number.isSafeInteger(registeredAt)||registeredAt<0||
    ![type,schema].every(x=>typeof x==='string'&&x.trim()===x&&family.test(x))||
    typeof raw!=='string'||Buffer.byteLength(raw)>maximumPayloadBytes||sha(raw)!==hash)return false;
 try{
  const payload=JSON.parse(raw);
  if(!payload||Array.isArray(payload)||typeof payload!=='object'||payload.projectId!==projectId||
     payload.schemaVersion!==schema||canonical(payload)!==raw)return false;
  if(!knownWorkflowSchema(type,schema))return true;
  // A known route with malformed/currently invalid inputs isn't proof of an
  // obsolete schema. It does not become terminal under this policy.
  return unsupportedPlannerPolicy(type,payload)&&canonicalWorkflowPayload(payload,projectId,type,schema)===raw;
 }catch{return false;}
}

export function ensurePendingReasonTable(db) {
 // Startup installs only this table; no pages or existing requests are read.
 db.exec(`CREATE TABLE IF NOT EXISTS official_request_terminal_reasons(
  official_request_id TEXT PRIMARY KEY NOT NULL,
  reason TEXT NOT NULL CHECK(reason='REQUEST_UNSUPPORTED_RECREATE')
 ) STRICT;`);
}

export function createPendingRetirement(db) {
 const transition=db.prepare(`UPDATE official_requests SET state='failed'
  WHERE rowid=? AND official_request_id=? AND source_id=? AND idempotency_key=?
    AND project_id='binari' AND state='registered' AND request_type=? AND schema_version=?
    AND payload_sha256=? AND payload_json=? AND registered_at=?`);
 const reason=db.prepare('INSERT INTO official_request_terminal_reasons VALUES(?,?)');
 return function retirePage(rows){
  const retired=rows.filter(unsupported);
  if(!retired.length)return new Set();
  db.exec('BEGIN IMMEDIATE');
  try{
   for(const row of retired){
    const {internalRowId,officialRequestId,sourceId,sourceKey,requestType,schemaVersion,payloadSha256,payloadJson,registeredAt}=row;
    // If a concurrent authoritative transition wins, leave it unchanged.
    if(transition.run(internalRowId,officialRequestId,sourceId,sourceKey,requestType,schemaVersion,
       payloadSha256,payloadJson,registeredAt).changes===1)reason.run(officialRequestId,unsupportedReason);
   }
   db.exec('COMMIT');
  }catch(error){db.exec('ROLLBACK');throw error;}
  return new Set(retired.map(r=>r.internalRowId));
 };
}
