/** Full-scene I2V request contract. Only an immutable project PNG digest is accepted. */
export const cinematicType='cinematic.generate',cinematicSchema='ngs.cinematic-generate.v1';
export const cinematicTask='cinematic_render',cinematicTaskType='cinematic.generate.render';
export const cinematicInvalid='NGS_CINEMATIC_PAYLOAD_INVALID',cinematicUnavailable='WORKFLOW_CINEMATIC_UNAVAILABLE';
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
