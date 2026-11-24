/** Standalone Request Service registration ledger, never the workflow/Common database. */
import {closeSync,constants,lstatSync,mkdirSync,openSync,realpathSync} from 'node:fs';
import {dirname,isAbsolute,join,resolve} from 'node:path';
import {DatabaseSync} from 'node:sqlite';
import {oneStopSchemas,requestType,validateCancel,validateStatusUpdate} from './one-stop-envelope.mjs';
import {validateOfficialSubmit,workflowScopeSql} from './request-envelope.mjs';
import {conceptCategoryRequestType,conceptCategorySchema} from './concept-category-envelope.mjs';
import {motion2DRequestType,motion2DSchema} from './motion-preset-convert-2d-envelope.mjs';
import {audioDeleteType,audioDeleteSchema} from './audio-delete-envelope.mjs';
import {audioSeparateType} from './audio-separate-envelope.mjs';
import {npcAnimationRegenerateType,npcAnimationRegenerateSchemas} from './npc-animation-regenerate-envelope.mjs';
import {audioRequestType,audioSchema} from './audio-generate-envelope.mjs';
import {initializeOfficialSchema} from './official-inbox-schema.mjs';
import {createRequestList,createWorkflowList,statuses} from './request-list.mjs';
import {allowedWorkflowSourceSql,officialIdFor} from './space-intake.mjs';

import {ensurePendingReasonTable,unsupportedPlannerPolicy} from './pending-unsupported.mjs';

import {propTypes,propGenerateType,propAnimationType,propGenerateUnavailable,propGenerateMotionUnavailable,propMutationUnavailable} from './prop-generate-envelope.mjs';
import {propImageType,propImageUnavailable} from './prop-image-envelope.mjs';

const error=()=>Error('OFFICIAL_REQUEST_INBOX_PRIVATE_REQUIRED');
export function validateInboxPath(directory){
 if(typeof directory!=='string'||!isAbsolute(directory)||resolve(directory)!==directory||dirname(directory)===directory)
  throw error();
 return directory;
}
export function openOfficialInbox(directory,{now=Date.now,readSpaceRequest,propImageEnabled=false,propGenerateEnabled=false,propGenerateMotionEnabled=false,propMutationsEnabled=false}={}){
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
 try{ensurePendingReasonTable(db);}catch(error){db.close();throw error;}
 const target=db.prepare(`SELECT official_request_id FROM official_requests
  WHERE project_id=? AND request_type='npc.workflow' AND schema_version IN (${oneStopSchemas.map(()=>'?').join(',')}) AND state IN ('registered','planned')
  AND (json_extract(payload_json,'$.character.npcId')=? COLLATE NOCASE
   OR json_extract(payload_json,'$.character.slug')=? COLLATE NOCASE) LIMIT 1`);
 const regenerationTarget=db.prepare(`SELECT 1 FROM official_requests
  WHERE project_id=? AND request_type=? AND schema_version IN (${npcAnimationRegenerateSchemas.map(()=>'?').join(',')}) AND state IN ('registered','planned')
  AND json_extract(payload_json,'$.npcId')=? COLLATE NOCASE LIMIT 1`);
 const targetCategory=db.prepare(`SELECT official_request_id FROM official_requests
  WHERE project_id=? AND request_type=? AND schema_version=? AND state IN ('registered','planned')
  AND json_extract(payload_json,'$.categoryId')=? COLLATE NOCASE LIMIT 1`);
 const motionVariant=db.prepare(`SELECT 1 FROM official_requests
  WHERE project_id=? AND request_type=? AND schema_version=? AND state IN ('registered','planned')
  AND json_extract(payload_json,'$.variantPresetId')=? COLLATE NOCASE LIMIT 1`);
 const audioSource=db.prepare(`SELECT 1 FROM official_requests
   WHERE official_request_id=? AND ${allowedWorkflowSourceSql} AND project_id=? AND request_type=? AND schema_version=? AND state='completed'
    AND json_extract(payload_json,'$.kind')='bgm'`);
 const activeAudioDelete=db.prepare(`SELECT 1 FROM official_requests
   WHERE ${allowedWorkflowSourceSql} AND project_id=? AND request_type=? AND schema_version=? AND state IN ('registered','planned')
    AND json_extract(payload_json,'$.officialRequestId')=?`);
 const insert=db.prepare(`INSERT INTO official_requests(
  official_request_id,source_id,project_id,idempotency_key,request_type,schema_version,
  payload_json,payload_sha256,state,registered_at) VALUES(?,?,?,?,?,?,?,?,?,?)`);
 const ownedStatus=db.prepare(`SELECT state FROM official_requests
  WHERE official_request_id=? AND ${allowedWorkflowSourceSql} AND project_id='binari'
  AND ${propMutationsEnabled===true?'1=1':`request_type NOT IN ('${propTypes[1]}','${propTypes[2]}')`}
  AND ${propGenerateMotionEnabled===true?'1=1':`request_type<>'${propAnimationType}' AND (request_type NOT IN ('${propGenerateType}','${propTypes[1]}') OR json_extract(payload_json,'$.motion') IS NOT 'subtle')`}
  AND ${propGenerateEnabled===true?'1=1':`request_type<>'${propGenerateType}'`}
  AND ${propImageEnabled===true?'1=1':`request_type<>'${propImageType}'`}
  AND ${workflowScopeSql}`);
 const localOwnedStatus=db.prepare(`SELECT state FROM official_requests
  WHERE official_request_id=? AND source_id='local' AND project_id='binari' AND ${workflowScopeSql}`);
 const cancelRow=db.prepare(`UPDATE official_requests SET state='cancelled'
  WHERE official_request_id=? AND source_id='local' AND project_id='binari'
   AND ${workflowScopeSql} AND state='registered'`);
 const transition=db.prepare(`UPDATE official_requests SET state=?
  WHERE official_request_id=? AND ${allowedWorkflowSourceSql} AND project_id='binari'
   AND ${workflowScopeSql} AND state=?`);
 function proof(prior,request){
  if(!prior||prior.official_request_id!==officialIdFor(request)||prior.request_type!==request.requestType||
     prior.schema_version!==request.schemaVersion||prior.payload_json!==request.payload||
     prior.payload_sha256!==request.payloadSha256)return null;
  if(!statuses.includes(prior.state)||!Number.isSafeInteger(prior.registered_at)||prior.registered_at<0)
   throw Error('OFFICIAL_REQUEST_REGISTER_FAILED');
  return {officialRequestId:prior.official_request_id,status:prior.state,registeredAt:prior.registered_at};
 }
 function register(request,isSite=false,raw){
   const {sourceId,projectId,requestType:kind,idempotencyKey,payload,payloadSha256,schemaVersion,npcId,npcSlug,categoryId}=request;
   if(propTypes.includes(kind)){
    if(propGenerateEnabled!==true)throw Error(propGenerateUnavailable);
    if(kind!==propGenerateType&&propMutationsEnabled!==true)throw Error(propMutationUnavailable);
    if((kind===propAnimationType||JSON.parse(payload).motion==='subtle')&&propGenerateMotionEnabled!==true)throw Error(propGenerateMotionUnavailable);
   }
   if(kind===propImageType&&propImageEnabled!==true)throw Error(propImageUnavailable);
   const officialRequestId=officialIdFor(request);
   let evidence;
   db.exec('BEGIN IMMEDIATE');
   try{
    const prior=original.get(sourceId,projectId,idempotencyKey);
    if(prior){
     evidence=proof(prior,request);
     if(!evidence||!isSite&&prior.state!=='registered')
      throw Error('OFFICIAL_REQUEST_IDEMPOTENCY_CONFLICT');
    }else{
     if(kind===conceptCategoryRequestType){
      const concept=raw?.payload??JSON.parse(payload);
      if(concept.operation==='replace'||concept.operation==='add'&&!Object.hasOwn(concept,'requiredSections'))
       throw Error('NGS_CONCEPT_CATEGORY_PAYLOAD_INVALID');
     }
     if(kind===requestType&&unsupportedPlannerPolicy(kind,JSON.parse(payload)))
      throw Error('REQUEST_UNSUPPORTED_RECREATE');
     if(kind===requestType||kind===npcAnimationRegenerateType){
      const slug=npcSlug??npcId.replace(/^(?:npc|character)\./u,'');
      if(target.get(projectId,...oneStopSchemas,npcId,slug)||
         regenerationTarget.get(projectId,npcAnimationRegenerateType,...npcAnimationRegenerateSchemas,npcId))
       throw Error('OFFICIAL_REQUEST_NPC_ALREADY_REGISTERED');
     }else if(kind===conceptCategoryRequestType&&targetCategory.get(projectId,conceptCategoryRequestType,conceptCategorySchema,categoryId)){
      throw Error('OFFICIAL_REQUEST_CONCEPT_CATEGORY_ALREADY_REGISTERED');
     }
     if(kind===motion2DRequestType&&motionVariant.get(projectId,kind,motion2DSchema,JSON.parse(payload).variantPresetId))
      throw Error('OFFICIAL_MOTION_2D_VARIANT_ALREADY_REGISTERED');
     if(kind===audioDeleteType){
      const audioId=JSON.parse(payload).officialRequestId;
      if(!audioSource.get(audioId,projectId,audioRequestType,audioSchema))throw Error('OFFICIAL_AUDIO_DELETE_TARGET_NOT_COMPLETED');
      if(activeAudioDelete.get(projectId,audioDeleteType,audioDeleteSchema,audioId))throw Error('OFFICIAL_AUDIO_DELETE_ALREADY_REGISTERED');
     }
     if(kind===audioSeparateType&&!audioSource.get(JSON.parse(payload).sourceOfficialRequestId,projectId,audioRequestType,audioSchema))
      throw Error('OFFICIAL_AUDIO_SEPARATE_SOURCE_NOT_COMPLETED');
     const registeredAt=now();
     insert.run(officialRequestId,sourceId,projectId,idempotencyKey,kind,schemaVersion,payload,payloadSha256,'registered',registeredAt);
     evidence={officialRequestId,status:'registered',registeredAt};
    }
    db.exec('COMMIT');
   }catch(error){db.exec('ROLLBACK');throw error;}
   return isSite?evidence:{officialRequestId,status:'registered'};
 }
 return {
  list:createRequestList(db),
  workflowList:createWorkflowList(db,{propImageEnabled,propGenerateEnabled,propGenerateMotionEnabled,propMutationsEnabled}),
  register:value=>register(validateOfficialSubmit(value),false,value),
  registerSpace:typeof readSpaceRequest==='function'?request=>register(request,true):undefined,
  cancel(value){
   const {officialRequestId}=validateCancel(value);
   db.exec('BEGIN IMMEDIATE');
   try{
    const prior=localOwnedStatus.get(officialRequestId);
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
