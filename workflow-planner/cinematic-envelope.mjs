/** Full-scene I2V request contract. Only an immutable project PNG digest is accepted. */
export const cinematicType='cinematic.generate',cinematicSchema='ngs.cinematic-generate.v1';
export const cinematicTask='cinematic_render',cinematicTaskType='cinematic.generate.render';
export const cinematicInvalid='NGS_CINEMATIC_PAYLOAD_INVALID',cinematicUnavailable='WORKFLOW_CINEMATIC_UNAVAILABLE';
export const cinematicLegacyRetired='WORKFLOW_CINEMATIC_MP4_INTAKE_RETIRED';
export const cinematicSourceMissing='WORKFLOW_CINEMATIC_SOURCE_NOT_FOUND';
const keys=['motionPrompt','projectId','schemaVersion','sourceSha256'];
const digest=/^[a-f0-9]{64}$/u;
export const cinematicPayloadSchema={type:'object',properties:{
 schemaVersion:{const:cinematicSchema},projectId:{const:'binari'},
 sourceSha256:{type:'string',pattern:'^[a-f0-9]{64}$',minLength:64,maxLength:64},
 motionPrompt:{type:'string',minLength:1,maxLength:1000},
},required:keys,additionalProperties:false};
export function validateCinematic(p,projectId){
 const v=p?.motionPrompt;
 if(!p||typeof p!=='object'||Array.isArray(p)||Object.keys(p).sort().join(',')!==keys.join(',')||
  p.schemaVersion!==cinematicSchema||p.projectId!==projectId||projectId!=='binari'||
  typeof p.sourceSha256!=='string'||p.sourceSha256.length!==64||!digest.test(p.sourceSha256)||
  typeof v!=='string'||!v.isWellFormed()||v.trim()!==v||![...v].length||[...v].length>1000||
  Buffer.byteLength(v,'utf8')>2000||/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]|\bembedding:/iu.test(v))
  throw Error(cinematicInvalid);
 return JSON.stringify(Object.fromEntries(keys.map(k=>[k,p[k]])));
}
export const cinematicResultRef=id=>'binari/cinematics/results/'+id+'.json';


export const cinematicSequenceType='cinematic.sequence.generate',cinematicSequenceSchema='ngs.cinematic-sequence-generate.v1';
export const cinematicSequenceTask='cinematic_sequence_render',cinematicSequenceTaskType='cinematic.sequence.generate.render';
const exact=(o,k)=>o!==null&&typeof o==='object'&&!Array.isArray(o)&&
 Object.keys(o).length===k.length&&k.every(x=>Object.hasOwn(o,x));
const bound=(v,min,max)=>typeof v==='number'&&Number.isFinite(v)&&v>=min&&v<=max;
const integer=(v,min,max)=>Number.isInteger(v)&&bound(v,min,max);
const sequenceKeys=[...keys,'playback'].sort();
const playbackKeys=['durationFrames','speedCurve','cameraCurve'];
const speedKeys=['frame','rate'], cameraKeys=['frame','zoom','x','y'];
const curveSchema=(points)=>({type:'array',minItems:1,maxItems:128,items:{type:'object',additionalProperties:false,
 required:Object.keys(points),properties:points}});
const frame={type:'integer',minimum:0,maximum:1439};
const numeric=(minimum,maximum)=>({type:'number',minimum,maximum});
export const cinematicPlaybackSchema={type:'object',additionalProperties:false,required:playbackKeys,properties:{
 durationFrames:{type:'integer',minimum:1,maximum:1440},
 speedCurve:curveSchema({frame,rate:numeric(0,4)}),
 cameraCurve:curveSchema({frame,zoom:numeric(1,3),x:numeric(0,1),y:numeric(0,1)}),
}};
export const cinematicSequencePayloadSchema={...cinematicPayloadSchema,required:sequenceKeys,properties:{
 ...cinematicPayloadSchema.properties,schemaVersion:{const:cinematicSequenceSchema},playback:cinematicPlaybackSchema}};

export function validCinematicPlayback(v){
 if(!exact(v,playbackKeys)||!integer(v.durationFrames,1,1440))return false;
 const points=(rows,fields,check)=>Array.isArray(rows)&&rows.length>=1&&rows.length<=128&&
  rows.every((p,i)=>exact(p,fields)&&integer(p.frame,0,v.durationFrames-1)&&
   (i===0?p.frame===0:p.frame>rows[i-1].frame)&&check(p));
 return points(v.speedCurve,speedKeys,p=>bound(p.rate,0,4))&&points(v.cameraCurve,cameraKeys,
  p=>bound(p.zoom,1,3)&&bound(p.x,0,1)&&bound(p.y,0,1));
}
function sortValue(v){
 if(Array.isArray(v))return v.map(sortValue);
 if(v!==null&&typeof v==='object')return Object.fromEntries(Object.keys(v).sort().map(k=>[k,sortValue(v[k])]));
 return v;
}
export function validateCinematicSequence(p,projectId){
 if(!exact(p,sequenceKeys)||p.schemaVersion!==cinematicSequenceSchema||!validCinematicPlayback(p.playback))
  throw Error(cinematicInvalid);
 const {playback,schemaVersion,...rest}=p;
 validateCinematic({...rest,schemaVersion:cinematicSchema},projectId);
 return JSON.stringify(sortValue(p));
}
export const cinematicSequenceResultRef=id=>'binari/cinematics/sequences/'+id+'/manifest.json';

export const cinematicPlaybackType='cinematic.playback.update',cinematicPlaybackRequestSchema='ngs.cinematic-playback-update.v1';
export const cinematicPlaybackTask='cinematic_playback_update',cinematicPlaybackStateSchema='ngs.cinematic-playback-state.v1';
export const cinematicPlaybackStale='WORKFLOW_CINEMATIC_PLAYBACK_CONFLICT';
const playbackRequestKeys=['schemaVersion','projectId','sequenceId','expectedRevision','playback'];
const sequenceId=/^request\.official\.[a-f0-9]{64}$/u;
export const cinematicPlaybackRequestPayloadSchema={type:'object',required:playbackRequestKeys,additionalProperties:false,properties:{
 schemaVersion:{const:cinematicPlaybackRequestSchema},projectId:{const:'binari'},
 sequenceId:{type:'string',minLength:81,maxLength:81,pattern:sequenceId.source},
 expectedRevision:{type:'integer',minimum:0,maximum:Number.MAX_SAFE_INTEGER-1},playback:cinematicPlaybackSchema,
}};
export function validateCinematicPlaybackUpdate(p,projectId){
 if(!exact(p,playbackRequestKeys)||p.schemaVersion!==cinematicPlaybackRequestSchema||p.projectId!==projectId||projectId!=='binari'||
  typeof p.sequenceId!=='string'||p.sequenceId.length!==81||!sequenceId.test(p.sequenceId)||
  !integer(p.expectedRevision,0,Number.MAX_SAFE_INTEGER-1)||!validCinematicPlayback(p.playback))
  throw Error(cinematicInvalid);
 return JSON.stringify(sortValue(p));
}
export const cinematicPlaybackResultRef=id=>'binari/cinematics/sequences/'+id+'/timeline.json';

export const cinematicSourceType='cinematic.source.generate',cinematicSourceRequestSchema='ngs.cinematic-source-generate.v1';
export const backgroundSourceType='background.source.generate',backgroundSourceRequestSchema='ngs.background-source-generate.v1';
export const backgroundPixelType='background.pixel.workflow',backgroundPixelSchema='ngs.background-pixel-workflow.v1';
export const cinematicArchiveType='cinematic.sequences.remove',cinematicArchiveRequestSchema='ngs.cinematic-sequences-remove.v1';
export const propAnimationRemoveType='prop.animation.remove',propAnimationRemoveRequestSchema='ngs.prop-animation-remove.v1';
const familyBase={projectId:{const:'binari'}}, hashSchema={type:'string',pattern:'^[a-f0-9]{64}$',minLength:64,maxLength:64};
const slugRE=/^[a-z0-9]+(?:[-._][a-z0-9]+)*$/u;
const slugSchema={type:'string',minLength:2,maxLength:80,pattern:slugRE.source};
const validSlug=s=>typeof s==='string'&&s.length>=2&&s.length<=80&&s.trim()===s&&slugRE.test(s);
const validHash=s=>typeof s==='string'&&s.length===64&&digest.test(s);
const cleanText=(s,chars,bytes)=>typeof s==='string'&&s.isWellFormed()&&s===s.trim()&&
 [...s].length>=1&&[...s].length<=chars&&Buffer.byteLength(s)<=bytes&&
 !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]|\bembedding:/iu.test(s);
const selection=items=>({type:'array',items,minItems:1,maxItems:30,uniqueItems:true});
const validSelection=(a,check)=>Array.isArray(a)&&a.length>=1&&a.length<=30&&a.every(check)&&new Set(a).size===a.length;
const req=(schema,fields)=>{const properties={...familyBase,schemaVersion:{const:schema},...fields};return {
 type:'object',additionalProperties:false,required:Object.keys(properties),properties,
};};
export const extraCinematicRequests=Object.freeze({
 [cinematicSourceType]:{schema:cinematicSourceRequestSchema,key:'cinematic_source_generate',worker:'image',route:'/v1/cinematics/ownership/workflow-source',
  capability:'workflow.cinematic-source-write.v1',
  payload:req(cinematicSourceRequestSchema,{title:{type:'string',minLength:1,maxLength:120},prompt:{type:'string',minLength:1,maxLength:2000}}),
  fields:['title','prompt'],valid:v=>cleanText(v.title,120,480)&&cleanText(v.prompt,2000,4000)},
 [backgroundSourceType]:{schema:backgroundSourceRequestSchema,key:'background_source_generate',worker:'image',route:'/v1/cinematics/ownership/workflow-background',
  capability:'workflow.background-source-write.v1',
  payload:req(backgroundSourceRequestSchema,Object.fromEntries(
   Object.entries({title:120,timeOfDay:80,season:80,mood:160,color:120,location:160,requirements:900,additionalNotes:900})
   .map(([k,maxLength])=>[k,{type:'string',minLength:k==='additionalNotes'?0:1,maxLength}]))),
  fields:['title','timeOfDay','season','mood','color','location','requirements','additionalNotes'],
  valid:v=>Object.entries({title:120,timeOfDay:80,season:80,mood:160,color:120,location:160,requirements:900,additionalNotes:900})
   .every(([k,n])=>k==='additionalNotes'&&v[k]===''||cleanText(v[k],n,n*4))},
 [backgroundPixelType]:{schema:backgroundPixelSchema,key:'background_pixel_postprocess',worker:'postprocess',route:'/v1/cinematics/ownership/workflow-background-pixel',
  capability:'workflow.background-pixel-write.v1',
  payload:req(backgroundPixelSchema,Object.fromEntries(
   Object.entries({title:105,timeOfDay:80,season:80,mood:160,color:120,location:160,requirements:900,additionalNotes:900})
   .map(([k,maxLength])=>[k,{type:'string',minLength:k==='additionalNotes'?0:1,maxLength}]))),
  fields:['title','timeOfDay','season','mood','color','location','requirements','additionalNotes'],
  valid:v=>Object.entries({title:105,timeOfDay:80,season:80,mood:160,color:120,location:160,requirements:900,additionalNotes:900})
   .every(([k,n])=>k==='additionalNotes'&&v[k]===''||cleanText(v[k],n,n*4))},
 [cinematicArchiveType]:{schema:cinematicArchiveRequestSchema,key:'cinematic_sequences_remove',worker:'file',route:'/v1/cinematics/ownership/workflow-sequence-remove',
  capability:'workflow.cinematic-sequence-remove.v1',
  payload:req(cinematicArchiveRequestSchema,{sequenceIds:selection({type:'string',minLength:81,maxLength:81,pattern:sequenceId.source})}),
  fields:['sequenceIds'],valid:v=>validSelection(v.sequenceIds,s=>typeof s==='string'&&s.length===81&&sequenceId.test(s))},
 [propAnimationRemoveType]:{schema:propAnimationRemoveRequestSchema,key:'prop_animation_remove',worker:'file',route:'/v1/props/ownership/workflow-animation-remove',
  capability:'workflow.prop-animation-remove.v1',
  payload:req(propAnimationRemoveRequestSchema,{propId:slugSchema,motionIds:selection(slugSchema),source:{type:'object',additionalProperties:false,
   required:['revision','sha256'],properties:{revision:{type:'integer',minimum:1,maximum:Number.MAX_SAFE_INTEGER-2},sha256:hashSchema}}}),
  fields:['propId','source','motionIds'],valid:v=>validSlug(v.propId)&&exact(v.source,['revision','sha256'])&&
   integer(v.source.revision,1,Number.MAX_SAFE_INTEGER-2)&&validHash(v.source.sha256)&&validSelection(v.motionIds,validSlug)},
});
export const extraCinematicTypes=Object.freeze(Object.keys(extraCinematicRequests));
export const allCinematicTypes=Object.freeze([cinematicType,cinematicSequenceType,cinematicPlaybackType,...extraCinematicTypes]);
export function validateExtraCinematic(payload,projectId,type){
 const cfg=Object.hasOwn(extraCinematicRequests,type)?extraCinematicRequests[type]:null;
 if(!cfg||!exact(payload,cfg.payload.required)||projectId!=='binari'||payload.projectId!==projectId||payload.schemaVersion!==cfg.schema||!cfg.valid(payload))
  throw Error(cinematicInvalid);
 return JSON.stringify(sortValue(payload));
}
