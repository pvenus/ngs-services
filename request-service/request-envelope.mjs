/** Known typed workflow families accepted by the Request Service MCP. */
import {ident,oneStopSchemas,requestType as npcRequestType,sha,validateOneStop} from './one-stop-envelope.mjs';
import {conceptCategoryRequestType,conceptCategorySchema,validateConceptCategory} from './concept-category-envelope.mjs';

import {npcPlanningUpdateType,npcPlanningUpdateSchema,validateNPCPlanningUpdate} from './npc-planning-update-envelope.mjs';

import {npcPlanningAddType,npcPlanningAddSchema,validateNPCPlanningAdd} from './npc-planning-add-envelope.mjs';

import {audioRequestType,audioSchema,validateAudioGenerate} from './audio-generate-envelope.mjs';
import {motion2DRequestType,motion2DSchema,validateMotion2D} from './motion-preset-convert-2d-envelope.mjs';
import {audioDeleteType,audioDeleteSchema,validateAudioDelete} from './audio-delete-envelope.mjs';
import {audioSeparateType,audioSeparateSchema,validateAudioSeparate} from './audio-separate-envelope.mjs';

import {npcAnimationRegenerateType,npcAnimationRegenerateSchemas,validateNpcAnimationRegenerate} from './npc-animation-regenerate-envelope.mjs';

const fields=['sourceId','requestType','idempotencyKey','payload'];
const workflowFamilies=new Map([
 [npcRequestType,{schemas:oneStopSchemas,validate:validateOneStop}],
 [npcAnimationRegenerateType,{schemas:npcAnimationRegenerateSchemas,validate:validateNpcAnimationRegenerate}],
 [conceptCategoryRequestType,{schemas:[conceptCategorySchema],validate:validateConceptCategory}],
 [npcPlanningUpdateType,{schemas:[npcPlanningUpdateSchema],validate:validateNPCPlanningUpdate}],
 [npcPlanningAddType,{schemas:[npcPlanningAddSchema],validate:validateNPCPlanningAdd}],
 [audioRequestType,{schemas:[audioSchema],validate:validateAudioGenerate}],
 [audioDeleteType,{schemas:[audioDeleteSchema],validate:validateAudioDelete}],
 [audioSeparateType,{schemas:[audioSeparateSchema],validate:validateAudioSeparate}],
 [motion2DRequestType,{schemas:[motion2DSchema],validate:validateMotion2D}],
]);
// All SQL fragments derive only from these fixed service constants, never
// the caller's type, schema or target. Bind every request value separately.
export const workflowScopeSql='('+[...workflowFamilies].map(([type,{schemas}])=>
 "(request_type='"+type+"' AND schema_version IN ("+schemas.map(schema=>"'"+schema+"'").join(',')+"))").join(' OR ')+')';

export const knownWorkflowSchema=(type,schema)=>workflowFamilies.get(type)?.schemas.includes(schema)===true;

export function canonicalWorkflowPayload(body,projectId,type,schema){
 const known=workflowFamilies.get(type);
 if(!known||!known.schemas.includes(schema)||body?.schemaVersion!==schema)
  throw Error('REQUEST_SUBMIT_INVALID');
 return known.validate(body,projectId);
}

function validateSubmit(value,sourceId,allowedType){
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).length!==fields.length||
  !fields.every(k=>Object.hasOwn(value,k))||value.sourceId!==sourceId||
  !allowedType(value.requestType)||!workflowFamilies.has(value.requestType)||!ident(value.idempotencyKey))throw Error('REQUEST_SUBMIT_INVALID');
 const projectId='binari',kind=value.requestType;
 // Preserve the legacy family-specific error for malformed NPC payloads.
 const payload=workflowFamilies.get(kind).validate(value.payload,projectId);
 return {sourceId:value.sourceId,projectId,requestType:kind,idempotencyKey:value.idempotencyKey,payload,
  payloadSha256:sha(payload),schemaVersion:value.payload.schemaVersion,
  ...(kind===npcRequestType?
   {npcId:value.payload.character.npcId,npcSlug:value.payload.character.slug}:
   kind===npcAnimationRegenerateType?{npcId:value.payload.npcId}:
   kind===conceptCategoryRequestType?{categoryId:value.payload.categoryId}:{})};
}

export const validateOfficialSubmit=value=>validateSubmit(value,'local',()=>true);
export const validateVerifiedSpaceSubmit=value=>validateSubmit(value,'space',()=>true);
