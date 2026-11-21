/** Read-only bounded views of the official request ledger. */
import {sha,workflowStatuses} from './one-stop-envelope.mjs';
import {canonicalWorkflowPayload,workflowScopeSql} from './request-envelope.mjs';
import {conceptCategoryRequestType} from './concept-category-envelope.mjs';
import {npcPlanningUpdateType} from './npc-planning-update-envelope.mjs';
import {npcPlanningAddType} from './npc-planning-add-envelope.mjs';
import {audioRequestType} from './audio-generate-envelope.mjs';
import {audioDeleteType} from './audio-delete-envelope.mjs';
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
const operatorFields=`${basic}, source_id AS sourceId,
  CASE request_type WHEN '${conceptCategoryRequestType}' THEN json_extract(payload_json,'$.categoryId')
   WHEN '${npcPlanningUpdateType}' THEN json_extract(payload_json,'$.npcId')
   WHEN '${npcPlanningAddType}' THEN json_extract(payload_json,'$.npcId')
   WHEN '${audioRequestType}' THEN json_extract(payload_json,'$.conceptId')
   WHEN '${audioDeleteType}' THEN json_extract(payload_json,'$.officialRequestId')
   ELSE json_extract(payload_json,'$.character.npcId') END AS targetNpcId,
  CASE request_type WHEN '${conceptCategoryRequestType}' THEN json_extract(payload_json,'$.title')
   WHEN '${npcPlanningUpdateType}' THEN json_extract(payload_json,'$.role')
   WHEN '${npcPlanningAddType}' THEN json_extract(payload_json,'$.title')
   WHEN '${audioRequestType}' THEN json_extract(payload_json,'$.title')
   WHEN '${audioDeleteType}' THEN coalesce((SELECT json_extract(g.payload_json,'$.title') FROM official_requests g
      WHERE g.official_request_id=json_extract(official_requests.payload_json,'$.officialRequestId')
       AND g.request_type='${audioRequestType}' AND g.source_id=official_requests.source_id AND g.project_id=official_requests.project_id),'BGM 삭제')
   ELSE json_extract(payload_json,'$.character.name') END AS targetName`;
const workflowFields=`${basic}, payload_json AS payloadJson, payload_sha256 AS payloadSha256`;
const commonKeys=['officialRequestId','projectId','requestType','schemaVersion'];
function workflowProjection(r){
 const {payloadJson,payloadSha256,...rest}=r;
 try{
  const payload=JSON.parse(payloadJson);
  if(rest.status!=='registered'||canonicalWorkflowPayload(payload,rest.projectId,rest.requestType,rest.schemaVersion)!==payloadJson||
     sha(payloadJson)!==payloadSha256)throw Error();
  return {...rest,payload,payloadSha256};
 }catch{throw Error('OFFICIAL_REQUEST_WORKFLOW_LIST_FAILED');}
}

function createPage(db,workflow){
 const highest=db.prepare('SELECT coalesce(MAX(rowid),0) AS horizon FROM official_requests');
 const page=db.prepare(`SELECT ${workflow?workflowFields:operatorFields}
  FROM official_requests
  WHERE rowid<=? AND (? IS NULL OR state=?)
   ${workflow?`AND source_id='local' AND project_id='binari' AND ${workflowScopeSql}`:""}
   AND (? IS NULL OR registered_at<? OR (registered_at=? AND rowid<?))
  ORDER BY registered_at DESC, rowid DESC LIMIT ?`);
 const invalid=()=>Error(workflow?'REQUEST_WORKFLOW_LIST_INVALID':'REQUEST_LIST_INVALID');
 const failed=()=>Error(workflow?'OFFICIAL_REQUEST_WORKFLOW_LIST_FAILED':'OFFICIAL_REQUEST_LIST_FAILED');
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
  const requests=rows.map(({internalRowId,...row})=>workflow?workflowProjection(row):row);
  return {requests,nextCursor:more?encode([1,scope,horizon,last.registeredAt,last.internalRowId]):null};
 };
}
export const createRequestList=db=>createPage(db,false);
export const createWorkflowList=db=>createPage(db,true);
