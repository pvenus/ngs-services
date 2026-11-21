/** Same-host, stateless Streamable HTTP MCP. Register, list and track durable requests without executing them. */
import {cancelTool,oneStopSchemas,submitTool,updateStatusTool,workflowStatuses} from './one-stop-envelope.mjs';
import {conceptCategoryRequestType,conceptCategorySchema,conceptCategoryAddIdPattern,conceptCategoryIdPattern,conceptCategoryGroupIdPattern} from './concept-category-envelope.mjs';
import {npcPlanningUpdateType,npcPlanningUpdateSchema,npcPlanningRoles,npcPlanningUpdateIdPattern,
 npcPlanningRequirementsLimit} from './npc-planning-update-envelope.mjs';
import {npcPlanningAddType,npcPlanningAddSchema,npcPlanningAddIdPattern,npcPlanningAddCategoryIdPattern} from './npc-planning-add-envelope.mjs';
import {audioRequestType,audioSchema,audioKind,audioTitleLimit,audioBriefLimit,audioVocalTextLimit,audioConceptIdPattern,audioDuration} from './audio-generate-envelope.mjs';
import {audioDeleteType,audioDeleteSchema,audioOfficialIdPattern} from './audio-delete-envelope.mjs';
import {defaultLimit,listTool,maxCursor,maxLimit,statuses,workflowListTool} from './request-list.mjs';

import {spaceQueueIdPattern,verifiedSpaceRequest} from './space-intake.mjs';

const version='2025-06-18';
const maxBody=96*1024;
const known={
 REQUEST_SPACE_INVALID:400,REQUEST_SPACE_NOT_VERIFIED:403,REQUEST_SUBMIT_INVALID:400,NGS_ONE_STOP_PAYLOAD_INVALID:400,NGS_CONCEPT_CATEGORY_PAYLOAD_INVALID:400,
 NGS_NPC_PLANNING_UPDATE_PAYLOAD_INVALID:400,NGS_NPC_PLANNING_ADD_PAYLOAD_INVALID:400,NGS_AUDIO_GENERATE_PAYLOAD_INVALID:400,NGS_AUDIO_DELETE_PAYLOAD_INVALID:400,
 OFFICIAL_AUDIO_DELETE_TARGET_NOT_COMPLETED:404,OFFICIAL_AUDIO_DELETE_ALREADY_REGISTERED:409,
 OFFICIAL_REQUEST_IDEMPOTENCY_CONFLICT:409,OFFICIAL_REQUEST_NPC_ALREADY_REGISTERED:409,
 OFFICIAL_REQUEST_CONCEPT_CATEGORY_ALREADY_REGISTERED:409,
 REQUEST_WORKFLOW_LIST_INVALID:400,REQUEST_STATUS_UPDATE_INVALID:400,OFFICIAL_REQUEST_STATUS_CONFLICT:409,
 REQUEST_LIST_INVALID:400,REQUEST_CANCEL_INVALID:400,OFFICIAL_REQUEST_NOT_FOUND:404,OFFICIAL_REQUEST_CANCEL_CONFLICT:409,
};
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const npcPayload={type:'object',properties:{schemaVersion:{enum:oneStopSchemas},projectId:{const:'binari'},
 intent:{type:'string'},character:{type:'object'},sourceRefs:{type:'array',items:{type:'string'}},
 includeCombat:{type:'boolean'},motionPresets:{type:'object'},combatMotionPresets:{type:'array',items:{type:'string'}}},
 required:['schemaVersion','projectId','intent','character','sourceRefs','includeCombat'],additionalProperties:false};
const conceptId={type:'string',pattern:conceptCategoryIdPattern},groupId={type:'string',pattern:conceptCategoryGroupIdPattern};
const conceptPayload={type:'object',properties:{schemaVersion:{const:conceptCategorySchema},projectId:{const:'binari'},
 operation:{enum:['add','update','migrate','remove']},categoryId:conceptId,
 groupId:{anyOf:[groupId,{type:'null'}]},title:{type:'string',minLength:1,maxLength:120},
 outline:{type:'string',maxLength:4000},requirements:{type:'string',maxLength:4000},
 requiredSections:{type:'array',minItems:1,maxItems:16,uniqueItems:true,items:{type:'string',minLength:1,maxLength:80}}},
 required:['schemaVersion','projectId','operation','categoryId','groupId','title','outline','requirements'],additionalProperties:false,
 allOf:[
  {if:{properties:{operation:{const:'add'}}},then:{required:['requiredSections'],properties:{categoryId:{type:'string',pattern:conceptCategoryAddIdPattern},groupId}},
   else:{properties:{groupId:{type:'null'}},not:{required:['requiredSections']}}},
  {if:{properties:{operation:{const:'add'}}},then:{properties:{outline:{minLength:1}}},
   else:{properties:{outline:{const:''}}}},
  {if:{properties:{operation:{const:'update'}}},then:{properties:{requirements:{minLength:1}}}},
  {if:{properties:{operation:{enum:['migrate','remove']}}},then:{properties:{requirements:{const:''}}}},
 ]};
const npcPlanningUpdatePayload={type:'object',properties:{schemaVersion:{const:npcPlanningUpdateSchema},projectId:{const:'binari'},
 npcId:{type:'string',pattern:npcPlanningUpdateIdPattern},role:{enum:npcPlanningRoles},
 requirements:{type:'string',minLength:1,maxLength:npcPlanningRequirementsLimit},
 },required:['schemaVersion','projectId','npcId','role','requirements'],additionalProperties:false};
const npcPlanningAddPayload={type:'object',properties:{
 schemaVersion:{const:npcPlanningAddSchema},projectId:{const:'binari'},npcId:{type:'string',pattern:npcPlanningAddIdPattern},
 categoryId:{type:'string',pattern:npcPlanningAddCategoryIdPattern},title:conceptPayload.properties.title,
 outline:{type:'string',minLength:1,maxLength:4000},requirements:conceptPayload.properties.requirements,
 requiredSections:conceptPayload.properties.requiredSections,
 },required:['schemaVersion','projectId','npcId','categoryId','title','outline','requirements','requiredSections'],additionalProperties:false};
const audioPayload={type:'object',properties:{
 schemaVersion:{const:audioSchema},projectId:{const:'binari'},kind:{const:audioKind},
 title:{type:'string',minLength:1,maxLength:audioTitleLimit},conceptId:{type:'string',pattern:audioConceptIdPattern},
 brief:{type:'string',minLength:1,maxLength:audioBriefLimit},text:{type:'string',maxLength:audioVocalTextLimit},
 durationSeconds:{anyOf:[{type:'number',minimum:audioDuration.min,maximum:audioDuration.max},{type:'null'}]},
 },required:['schemaVersion','projectId','kind','title','conceptId','brief','text','durationSeconds'],additionalProperties:false,
 allOf:[{if:{properties:{durationSeconds:{type:'number'}}},then:{properties:{text:{const:''}}}}]};
const audioDeletePayload={type:'object',properties:{
 schemaVersion:{const:audioDeleteSchema},projectId:{const:'binari'},officialRequestId:{type:'string',pattern:audioOfficialIdPattern,minLength:81,maxLength:81},
 },required:['schemaVersion','projectId','officialRequestId'],additionalProperties:false};
const tool={
 name:submitTool,description:'Register an exact typed NPC, NPC document add/update, concept category, BGM generation, or BGM removal workflow without executing it.',
 inputSchema:{type:'object',properties:{
  sourceId:{const:'local'},requestType:{enum:['npc.workflow',conceptCategoryRequestType,npcPlanningUpdateType,npcPlanningAddType,audioRequestType,audioDeleteType]},
  idempotencyKey:{type:'string',minLength:1,maxLength:200},payload:{type:'object'},
 },required:['sourceId','requestType','idempotencyKey','payload'],additionalProperties:false,
 oneOf:[{properties:{requestType:{const:'npc.workflow'},payload:npcPayload}},
  {properties:{requestType:{const:conceptCategoryRequestType},payload:conceptPayload}},
  {properties:{requestType:{const:npcPlanningUpdateType},payload:npcPlanningUpdatePayload}},
  {properties:{requestType:{const:npcPlanningAddType},payload:npcPlanningAddPayload}},
  {properties:{requestType:{const:audioRequestType},payload:audioPayload}},
  {properties:{requestType:{const:audioDeleteType},payload:audioDeletePayload}}]},
 outputSchema:{type:'object',properties:{officialRequestId:{type:'string',pattern:'^request\\.official\\.[a-f0-9]{64}$'},
  status:{const:'registered'}},required:['officialRequestId','status'],additionalProperties:false},
 annotations:{readOnlyHint:false,destructiveHint:false,idempotentHint:true,openWorldHint:false},
};
const cancel={
 name:cancelTool,description:'Mark one exact locally registered request cancelled, keeping its record and payload.',
 inputSchema:{type:'object',properties:{officialRequestId:tool.outputSchema.properties.officialRequestId},
  required:['officialRequestId'],additionalProperties:false},
 outputSchema:{...tool.outputSchema,properties:{...tool.outputSchema.properties,status:{const:'cancelled'}}},
 annotations:{readOnlyHint:false,destructiveHint:false,idempotentHint:true,openWorldHint:false},
};
const rowFields=['officialRequestId','sourceId','projectId','requestType','schemaVersion','status','registeredAt','targetNpcId','targetName'];
const list={
 name:listTool,description:'List a bounded page of official requests for local OPS, without their payloads.',
 inputSchema:{type:'object',properties:{
  status:{enum:statuses},limit:{type:'integer',minimum:1,maximum:maxLimit,default:defaultLimit},
  cursor:{type:'string',minLength:1,maxLength:maxCursor},
 },additionalProperties:false},
 outputSchema:{type:'object',properties:{
  requests:{type:'array',items:{type:'object',properties:{
   ...Object.fromEntries(rowFields.map(k=>[k,{type:'string'}])),
   status:{enum:statuses},registeredAt:{type:'integer',minimum:0},
  },required:rowFields,additionalProperties:false}},
  nextCursor:{type:['string','null']},
 },required:['requests','nextCursor'],additionalProperties:false},
 annotations:{readOnlyHint:true,destructiveHint:false,idempotentHint:true,openWorldHint:false},
};
const workflowFields=['officialRequestId','projectId','requestType','schemaVersion','payload','payloadSha256','status','registeredAt'];
const workflowList={
 name:workflowListTool,description:'Read a local page of eligible registered requests with their immutable typed payload.',
 inputSchema:{...list.inputSchema,properties:{limit:list.inputSchema.properties.limit,cursor:list.inputSchema.properties.cursor}},
 outputSchema:{...list.outputSchema,properties:{...list.outputSchema.properties,
  requests:{type:'array',items:{type:'object',properties:{
   ...Object.fromEntries(workflowFields.map(k=>[k,{type:'string'}])),
   payload:{type:'object'},payloadSha256:{type:'string',pattern:'^[a-f0-9]{64}$'},
   status:{const:'registered'},registeredAt:{type:'integer',minimum:0},sourceId:{const:'space'},sourceKey:{type:'string',pattern:spaceQueueIdPattern},
  },required:workflowFields,additionalProperties:false}},
 }},
 annotations:list.annotations,
};
const updateStatus={
 name:updateStatusTool,description:'Record an allowed workflow status transition for one exact official request.',
 inputSchema:{type:'object',properties:{officialRequestId:tool.outputSchema.properties.officialRequestId,
  status:{enum:workflowStatuses}},required:['officialRequestId','status'],additionalProperties:false},
 outputSchema:{...tool.outputSchema,properties:{...tool.outputSchema.properties,status:{enum:workflowStatuses}}},
 annotations:tool.annotations,
};
const siteToolSchema={
 name:'request_submit_space',description:'Verify one typed request via the configured owner Space MCP before local intake or exact replay.',
 inputSchema:{type:'object',properties:{queueId:{type:'string',pattern:spaceQueueIdPattern}},
  required:['queueId'],additionalProperties:false},
 outputSchema:{...tool.outputSchema,properties:{...tool.outputSchema.properties,status:{enum:statuses},
  registeredAt:{type:'integer',minimum:0}},required:['officialRequestId','status','registeredAt']},
 annotations:tool.annotations,
};
const toolBindings=new Map([
 [submitTool,['register','OFFICIAL_REQUEST_REGISTER_FAILED']],
 [cancelTool,['cancel','OFFICIAL_REQUEST_CANCEL_FAILED']],
 [listTool,['list','OFFICIAL_REQUEST_LIST_FAILED']],
 [workflowListTool,['workflowList','OFFICIAL_REQUEST_WORKFLOW_LIST_FAILED']],
 [updateStatusTool,['updateStatus','OFFICIAL_REQUEST_STATUS_UPDATE_FAILED']],
]);
function send(res,status,body,headers={}){
 const raw=body===undefined?Buffer.alloc(0):Buffer.from(JSON.stringify(body));
 res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Content-Length':String(raw.length),
  'X-Content-Type-Options':'nosniff','Cache-Control':'no-store',...headers}).end(raw);
}
const result=(res,id,value)=>send(res,200,{jsonrpc:'2.0',id,result:value});
const rpcError=(res,id,code,message,status=200)=>send(res,status,{jsonrpc:'2.0',id,error:{code,message}});
const text=value=>[{type:'text',text:JSON.stringify(value)}];
function allowed(req,port){
 return req.socket.localAddress==='127.0.0.1'&&req.socket.remoteAddress==='127.0.0.1'&&
  req.headers.host==='127.0.0.1:'+port&&
  !['origin','cookie','sec-fetch-site','transfer-encoding','forwarded','x-forwarded-for','x-forwarded-host','x-forwarded-proto'].some(h=>req.headers[h]!==undefined);
}
async function body(req){
 const len=req.headers['content-length'];
 if(typeof len!=='string'||!/^\d+$/.test(len)||Number(len)>maxBody||Number(len)<1)throw Error();
 const media=req.headers['content-type'];
 if(typeof media!=='string'||!/^application\/json(?:\s*;|$)/i.test(media))throw Error();
 const chunks=[];let size=0;
 for await(const chunk of req){
  size+=chunk.length;
  if(size>Number(len))throw Error();
  chunks.push(chunk);
 }
 if(size!==Number(len))throw Error();
 return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(chunks,size)));
}
export async function requestMcp(req,res,inbox,{readSpaceRequest}={}){
 if(!allowed(req,req.socket.localPort))return send(res,403,{error:'REQUEST_MCP_LOOPBACK_REQUIRED'});
 if(req.url!=='/mcp')return send(res,404,{error:'NOT_FOUND'});
 if(req.method!=='POST')return send(res,405,{error:'REQUEST_MCP_POST_ONLY'},{Allow:'POST'});
 const accepted=new Set(String(req.headers.accept??'').toLowerCase().split(',').map(v=>v.split(';',1)[0].trim()));
 if(!accepted.has('application/json')||!accepted.has('text/event-stream'))return send(res,406,{error:'REQUEST_MCP_ACCEPT_REQUIRED'});
 if(req.headers['mcp-session-id']!==undefined||
   req.headers['mcp-protocol-version']!==undefined&&req.headers['mcp-protocol-version']!==version)
  return send(res,400,{error:'REQUEST_MCP_PROTOCOL_INVALID'});
 let m;
 try{m=await body(req);}catch{return rpcError(res,null,-32700,'Invalid JSON or body',400);}
 if(!object(m)||m.jsonrpc!=='2.0'||typeof m.method!=='string'||
   Object.keys(m).some(k=>!['jsonrpc','id','method','params'].includes(k))||
   Object.hasOwn(m,'id')&&!(typeof m.id==='string'||Number.isSafeInteger(m.id)))
  return rpcError(res,null,-32600,'Invalid request',400);
 if(!Object.hasOwn(m,'id'))return send(res,202); // Never executes a request via notifications.
 const {id,method,params={}}=m;
 if(!object(params))return rpcError(res,id,-32602,'Invalid parameters');
 if(method==='initialize'){
  if(!object(params.capabilities)||!object(params.clientInfo)||typeof params.protocolVersion!=='string'||
    typeof params.clientInfo.name!=='string'||typeof params.clientInfo.version!=='string')
   return rpcError(res,id,-32602,'Invalid initialize parameters');
  return result(res,id,{protocolVersion:version,capabilities:{tools:{}},serverInfo:{name:'vinus-request-service',version:'1.0.0'}});
 }
 if(method==='ping'&&!Object.keys(params).length)return result(res,id,{});
 if(method==='tools/list'&&!Object.keys(params).length)return result(res,id,{tools:[tool,cancel,list,workflowList,updateStatus,...(readSpaceRequest?[siteToolSchema]:[])]});
 if(method!=='tools/call')return rpcError(res,id,-32601,'Method not found');
 const site=readSpaceRequest&&params.name===siteToolSchema.name;
 const binding=site?['registerSpace','OFFICIAL_REQUEST_SPACE_FAILED']:toolBindings.get(params.name);
 if(!binding||!Object.hasOwn(params,'arguments')||
    Object.keys(params).some(k=>!['name','arguments','_meta'].includes(k)))
  return rpcError(res,id,-32602,'Unknown tool or invalid tool call');
 try{
  const args=site?await verifiedSpaceRequest(params.arguments,readSpaceRequest):params.arguments;
  const value=inbox[binding[0]](args);
  return result(res,id,{isError:false,content:text(value),structuredContent:value});
 }catch(err){
  const matched=Object.hasOwn(known,err?.message);
  const fallback=binding[1];
  const failure={error:matched?err.message:fallback,status:matched?known[err.message]:503};
  return result(res,id,{isError:true,content:text(failure),structuredContent:failure});
 }
}
