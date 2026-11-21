/** Standalone Request Service registration ledger, never the workflow/Common database. */
import {closeSync,constants,lstatSync,mkdirSync,openSync,realpathSync} from 'node:fs';
import {dirname,isAbsolute,join,resolve} from 'node:path';
import {DatabaseSync} from 'node:sqlite';
import {canonical,oneStopSchemas,requestType,sha,validateCancel,validateStatusUpdate} from './one-stop-envelope.mjs';
import {validateOfficialSubmit,workflowScopeSql} from './request-envelope.mjs';
import {conceptCategoryRequestType,conceptCategorySchema} from './concept-category-envelope.mjs';
import {audioDeleteType,audioDeleteSchema} from './audio-delete-envelope.mjs';
import {audioRequestType,audioSchema} from './audio-generate-envelope.mjs';
import {initializeOfficialSchema} from './official-inbox-schema.mjs';
import {createRequestList,createWorkflowList} from './request-list.mjs';

const error=()=>Error('OFFICIAL_REQUEST_INBOX_PRIVATE_REQUIRED');
export function validateInboxPath(directory){
 if(typeof directory!=='string'||!isAbsolute(directory)||resolve(directory)!==directory||dirname(directory)===directory)
  throw error();
 return directory;
}
export function openOfficialInbox(directory,{now=Date.now}={}){
 validateInboxPath(directory);
 if(realpathSync(dirname(directory))!==dirname(directory))throw error();
 try{mkdirSync(directory,{mode:0o700});}catch(e){if(e.code!=='EEXIST')throw e;}
 const folder=lstatSync(directory);
 if(!folder.isDirectory()||folder.uid!==process.getuid()||(folder.mode&0o07777)!==0o700||
    realpathSync(directory)!==directory)throw error();
 const path=join(directory,'official-requests.sqlite');
 try{closeSync(openSync(path,constants.O_CREAT|constants.O_EXCL|constants.O_RDWR|constants.O_NOFOLLOW,0o600));}
 catch(e){if(e.code!=='EEXIST')throw e;}
 const st=lstatSync(path);
 if(!st.isFile()||st.uid!==process.getuid()||(st.mode&0o07777)!==0o600||st.nlink!==1)throw error();
 const db=new DatabaseSync(path);
 try{initializeOfficialSchema(db);}catch(error){db.close();throw error;}
 const original=db.prepare(`SELECT * FROM official_requests
  WHERE source_id=? AND project_id=? AND idempotency_key=?`);
 const target=db.prepare(`SELECT official_request_id FROM official_requests
  WHERE project_id=? AND request_type='npc.workflow' AND schema_version IN (?,?) AND state IN ('registered','planned')
  AND (json_extract(payload_json,'$.character.npcId')=? COLLATE NOCASE
   OR json_extract(payload_json,'$.character.slug')=? COLLATE NOCASE) LIMIT 1`);
 const targetCategory=db.prepare(`SELECT official_request_id FROM official_requests
  WHERE project_id=? AND request_type=? AND schema_version=? AND state IN ('registered','planned')
  AND json_extract(payload_json,'$.categoryId')=? COLLATE NOCASE LIMIT 1`);
 const audioSource=db.prepare(`SELECT 1 FROM official_requests
   WHERE official_request_id=? AND source_id='local' AND project_id=? AND request_type=? AND schema_version=? AND state='completed'
    AND json_extract(payload_json,'$.kind')='bgm'`);
 const activeAudioDelete=db.prepare(`SELECT 1 FROM official_requests
   WHERE source_id='local' AND project_id=? AND request_type=? AND schema_version=? AND state IN ('registered','planned')
    AND json_extract(payload_json,'$.officialRequestId')=?`);
 const insert=db.prepare(`INSERT INTO official_requests(
  official_request_id,source_id,project_id,idempotency_key,request_type,schema_version,
  payload_json,payload_sha256,state,registered_at) VALUES(?,?,?,?,?,?,?,?,?,?)`);
 const ownedStatus=db.prepare(`SELECT state FROM official_requests
  WHERE official_request_id=? AND source_id='local' AND project_id='binari'
  AND ${workflowScopeSql}`);
 const cancelRow=db.prepare(`UPDATE official_requests SET state='cancelled'
  WHERE official_request_id=? AND source_id='local' AND project_id='binari'
   AND ${workflowScopeSql} AND state='registered'`);
 const transition=db.prepare(`UPDATE official_requests SET state=?
  WHERE official_request_id=? AND source_id='local' AND project_id='binari'
   AND ${workflowScopeSql} AND state=?`);
 return {
  list:createRequestList(db),
  workflowList:createWorkflowList(db),
  register(value){
   const {sourceId,projectId,requestType:kind,idempotencyKey,payload,payloadSha256,schemaVersion,npcId,npcSlug,categoryId}=validateOfficialSubmit(value);
   const officialRequestId='request.official.'+sha(canonical(['request.official.v1',sourceId,projectId,idempotencyKey]));
   db.exec('BEGIN IMMEDIATE');
   try{
    const prior=original.get(sourceId,projectId,idempotencyKey);
    if(prior){
     if(prior.official_request_id!==officialRequestId||prior.request_type!==kind||prior.schema_version!==schemaVersion||
        prior.payload_json!==payload||prior.payload_sha256!==payloadSha256||prior.state!=='registered')
      throw Error('OFFICIAL_REQUEST_IDEMPOTENCY_CONFLICT');
    }else{
     if(kind===conceptCategoryRequestType&&(value.payload.operation==='replace'||
        (value.payload.operation==='add'&&!Object.hasOwn(value.payload,'requiredSections'))))
      throw Error('NGS_CONCEPT_CATEGORY_PAYLOAD_INVALID');
     if(kind===requestType){
      if(target.get(projectId,...oneStopSchemas,npcId,npcSlug))throw Error('OFFICIAL_REQUEST_NPC_ALREADY_REGISTERED');
     }else if(kind===conceptCategoryRequestType&&targetCategory.get(projectId,conceptCategoryRequestType,conceptCategorySchema,categoryId)){
      throw Error('OFFICIAL_REQUEST_CONCEPT_CATEGORY_ALREADY_REGISTERED');
     }
     if(kind===audioDeleteType){
      const audioId=value.payload.officialRequestId;
      if(!audioSource.get(audioId,projectId,audioRequestType,audioSchema))throw Error('OFFICIAL_AUDIO_DELETE_TARGET_NOT_COMPLETED');
      if(activeAudioDelete.get(projectId,audioDeleteType,audioDeleteSchema,audioId))throw Error('OFFICIAL_AUDIO_DELETE_ALREADY_REGISTERED');
     }
     insert.run(officialRequestId,sourceId,projectId,idempotencyKey,kind,schemaVersion,payload,payloadSha256,'registered',now());
    }
    db.exec('COMMIT');
   }catch(error){db.exec('ROLLBACK');throw error;}
   return {officialRequestId,status:'registered'};
  },
  cancel(value){
   const {officialRequestId}=validateCancel(value);
   db.exec('BEGIN IMMEDIATE');
   try{
    const prior=ownedStatus.get(officialRequestId);
    if(!prior)throw Error('OFFICIAL_REQUEST_NOT_FOUND');
    if(prior.state!=='cancelled'&&(prior.state!=='registered'||cancelRow.run(officialRequestId).changes!==1))
     throw Error('OFFICIAL_REQUEST_CANCEL_CONFLICT');
    db.exec('COMMIT');
   }catch(error){db.exec('ROLLBACK');throw error;}
   return {officialRequestId,status:'cancelled'};
  },
  updateStatus(value){
   const {officialRequestId,status}=validateStatusUpdate(value);
   db.exec('BEGIN IMMEDIATE');
   try{
    const row=ownedStatus.get(officialRequestId);
    if(!row)throw Error('OFFICIAL_REQUEST_NOT_FOUND');
    if(row.state!==status){
     const allowed=status==='planned'?row.state==='registered':row.state==='planned';
     if(!allowed||transition.run(status,officialRequestId,row.state).changes!==1)
      throw Error('OFFICIAL_REQUEST_STATUS_CONFLICT');
    }
    db.exec('COMMIT');
   }catch(error){db.exec('ROLLBACK');throw error;}
   return {officialRequestId,status};
  },
  close(){db.close();},
 };
}
