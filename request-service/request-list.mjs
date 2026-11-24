/** Operator views and bounded Planner handoff of the official request ledger. */
import {sha,workflowStatuses} from './one-stop-envelope.mjs';
import {canonicalWorkflowPayload,knownWorkflowSchema} from './request-envelope.mjs';
import {conceptCategoryRequestType} from './concept-category-envelope.mjs';
import {npcPlanningUpdateType} from './npc-planning-update-envelope.mjs';
import {npcPlanningAddType} from './npc-planning-add-envelope.mjs';
import {audioRequestType} from './audio-generate-envelope.mjs';
import {motion2DRequestType} from './motion-preset-convert-2d-envelope.mjs';
import {npcAnimationRegenerateType} from './npc-animation-regenerate-envelope.mjs';
import {audioDeleteType} from './audio-delete-envelope.mjs';
import {audioSeparateType} from './audio-separate-envelope.mjs';
import {allowedWorkflowSourceSql} from './space-intake.mjs';
import {createPendingRetirement,unsupportedReason} from './pending-unsupported.mjs';
import {propTypes,propGenerateType,propRegenerateType,propAnimationType} from './prop-generate-envelope.mjs';
import {cinematicType} from './cinematic-envelope.mjs';
import {propImageType} from './prop-image-envelope.mjs';
export const listTool='request_list',workflowListTool='request_workflow_list';
export const statuses=Object.freeze(['registered',...workflowStatuses,'cancelled']);
export const defaultLimit=50,maxLimit=100,maxCursor=160;
const int=(v,min)=>Number.isSafeInteger(v)&&v>=min;
const encode=value=>Buffer.from(JSON.stringify(value)).toString('base64url');

function readCursor(value,scope,invalid){
 if(typeof value!=='string'||!value.length||value.length>maxCursor||/[^A-Za-z0-9_-]/.test(value))throw invalid();
 let cursor;
 try{cursor=JSON.parse(Buffer.from(value,'base64url').toString());}catch{throw invalid();}
 if(!Array.isArray(cursor)||cursor.length!==5||cursor[0]!==1||cursor[1]!==scope||
    !int(cursor[2],1)||!int(cursor[3],0)||!int(cursor[4],1)||cursor[4]>cursor[2]||encode(cursor)!==value)throw invalid();
 return cursor;
}
const basic=`rowid AS internalRowId, official_request_id AS officialRequestId,
  project_id AS projectId, request_type AS requestType, schema_version AS schemaVersion,
  state AS status, registered_at AS registeredAt`;
// targetNpcId is the legacy OPS display key. Keep its DTO stable for old
// clients; the typed request family distinguishes a category target from an NPC.
const terminalReason=`(SELECT reason FROM official_request_terminal_reasons
 WHERE official_request_id=official_requests.official_request_id AND official_requests.state='failed')`;
const operatorFields=`${basic}, source_id AS sourceId, ${terminalReason} AS failureReason,
  CASE WHEN ${terminalReason}='${unsupportedReason}' THEN official_request_id
  ELSE CASE request_type WHEN '${conceptCategoryRequestType}' THEN json_extract(payload_json,'$.categoryId')
   WHEN '${npcPlanningUpdateType}' THEN json_extract(payload_json,'$.npcId')
   WHEN '${npcPlanningAddType}' THEN json_extract(payload_json,'$.npcId')
   WHEN '${audioRequestType}' THEN json_extract(payload_json,'$.conceptId')
   WHEN '${audioDeleteType}' THEN json_extract(payload_json,'$.officialRequestId')
   WHEN '${audioSeparateType}' THEN json_extract(payload_json,'$.sourceOfficialRequestId')
   WHEN '${propRegenerateType}' THEN json_extract(payload_json,'$.propId')
   WHEN '${propAnimationType}' THEN json_extract(payload_json,'$.propId')
   WHEN '${cinematicType}' THEN official_request_id
   WHEN '${propGenerateType}' THEN json_extract(payload_json,'$.propId')
   WHEN '${propImageType}' THEN json_extract(payload_json,'$.propId')
   WHEN '${motion2DRequestType}' THEN json_extract(payload_json,'$.variantPresetId')
   WHEN '${npcAnimationRegenerateType}' THEN json_extract(payload_json,'$.npcId')
   ELSE json_extract(payload_json,'$.character.npcId') END END AS targetNpcId,
  CASE WHEN ${terminalReason}='${unsupportedReason}' THEN '지원 종료 요청'
  ELSE CASE request_type WHEN '${conceptCategoryRequestType}' THEN json_extract(payload_json,'$.title')
   WHEN '${npcPlanningUpdateType}' THEN json_extract(payload_json,'$.role')
   WHEN '${npcPlanningAddType}' THEN json_extract(payload_json,'$.title')
   WHEN '${audioRequestType}' THEN json_extract(payload_json,'$.title')
   WHEN '${audioDeleteType}' THEN coalesce((SELECT json_extract(g.payload_json,'$.title') FROM official_requests g
      WHERE g.official_request_id=json_extract(official_requests.payload_json,'$.officialRequestId')
       AND g.request_type='${audioRequestType}' AND g.project_id=official_requests.project_id),'BGM 삭제')
   WHEN '${audioSeparateType}' THEN '음원 분리'
   WHEN '${motion2DRequestType}' THEN json_extract(payload_json,'$.label')
   WHEN '${npcAnimationRegenerateType}' THEN '애니메이션 생성'
   WHEN '${propRegenerateType}' THEN '프랍 재생성'
   WHEN '${propAnimationType}' THEN '프랍 애니메이션 추가'
   WHEN '${cinematicType}' THEN '시네마틱 영상 생성'
   WHEN '${propGenerateType}' THEN '프랍 패키지 생성'
   WHEN '${propImageType}' THEN '프랍 이미지 생성'
   ELSE json_extract(payload_json,'$.character.name') END END AS targetName`;
const workflowFields=`${basic}, payload_json AS payloadJson, payload_sha256 AS payloadSha256, source_id AS sourceId, idempotency_key AS sourceKey`;
const commonKeys=['officialRequestId','projectId','requestType','schemaVersion'];
function workflowProjection(r){
 const {payloadJson,payloadSha256,sourceId,sourceKey,...rest}=r;
 try{
  const payload=JSON.parse(payloadJson);
  if(rest.status!=='registered'||canonicalWorkflowPayload(payload,rest.projectId,rest.requestType,rest.schemaVersion)!==payloadJson||
     sha(payloadJson)!==payloadSha256)throw Error();
  return {...rest,payload,payloadSha256,...(sourceId==='space'?{sourceId,sourceKey}: {})};
 }catch{throw Error('OFFICIAL_REQUEST_WORKFLOW_LIST_FAILED');}
}

function createPage(db,workflow,{propImageEnabled=false,propGenerateEnabled=false,propGenerateMotionEnabled=false,propAnimationEnabled=propGenerateMotionEnabled,propMutationsEnabled=false,cinematicEnabled=false}={}){
 const highest=db.prepare('SELECT coalesce(MAX(rowid),0) AS horizon FROM official_requests');
 const page=db.prepare(`SELECT ${workflow?workflowFields:operatorFields}
  FROM official_requests
  WHERE rowid<=? AND (? IS NULL OR state=?)
   ${workflow?`AND ${allowedWorkflowSourceSql} AND project_id='binari'`:""}
   AND (? IS NULL OR registered_at<? OR (registered_at=? AND rowid<?))
  ORDER BY registered_at DESC, rowid DESC LIMIT ?`);
 const invalid=()=>Error(workflow?'REQUEST_WORKFLOW_LIST_INVALID':'REQUEST_LIST_INVALID');
 const failed=()=>Error(workflow?'OFFICIAL_REQUEST_WORKFLOW_LIST_FAILED':'OFFICIAL_REQUEST_LIST_FAILED');
 const retirePage=workflow?createPendingRetirement(db):null;
 const inputs=workflow?['limit','cursor']:['status','limit','cursor'];
 return function list(value){
  if(!value||typeof value!=='object'||Array.isArray(value)||
     Object.keys(value).some(k=>!inputs.includes(k)))throw invalid();
  const status=workflow?'registered':(Object.hasOwn(value,'status')?value.status:null);
  const scope=workflow?'workflow:registered':status;
  const limit=Object.hasOwn(value,'limit')?value.limit:defaultLimit;
  if((Object.hasOwn(value,'status')&&!statuses.includes(status))||!int(limit,1)||limit>maxLimit)throw invalid();
  const cursor=Object.hasOwn(value,'cursor')?readCursor(value.cursor,scope,invalid):null;
  const horizon=cursor?.[2]??highest.get().horizon,at=cursor?.[3]??null,rowId=cursor?.[4]??null;
  const rows=page.all(horizon,status,status,at,at,at,rowId,limit+1);
  const keys=workflow?commonKeys:[...commonKeys,'sourceId','targetNpcId','targetName'];
  if(!rows.every(r=>int(r.internalRowId,1)&&int(r.registeredAt,0)&&statuses.includes(r.status)&&
     keys.every(k=>typeof r[k]==='string')))throw failed();
  const more=rows.length>limit;
  if(more)rows.pop();
  const last=rows.at(-1);
  let requests;
  if(workflow){
   // Cursors follow the examined rows even when an entire page retires.
   const retired=retirePage(rows);
   requests=rows.filter(row=>!retired.has(row.internalRowId)&&knownWorkflowSchema(row.requestType,row.schemaVersion))
    .filter(row=>!propTypes.includes(row.requestType)||propGenerateEnabled===true&&
     (row.requestType===propGenerateType||propMutationsEnabled===true)&&
     (row.requestType===propAnimationType?propAnimationEnabled===true:
      JSON.parse(row.payloadJson).motion!=='subtle'||propGenerateMotionEnabled===true))
    .filter(row=>row.requestType!==cinematicType||cinematicEnabled===true)
    .filter(row=>row.requestType!==propImageType||propImageEnabled===true)
    .map(({internalRowId,...row})=>workflowProjection(row));
  }else requests=rows.map(({internalRowId,failureReason,...row})=>
   failureReason===unsupportedReason?{...row,failureReason}:row);
  return {requests,nextCursor:more?encode([1,scope,horizon,last.registeredAt,last.internalRowId]):null};
 };
}
export const createRequestList=db=>createPage(db,false);
export const createWorkflowList=(db,options)=>createPage(db,true,options);
