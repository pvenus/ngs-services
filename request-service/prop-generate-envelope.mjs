/** Formal one-prop request: authoring intent, not worker/render knobs. Copy verbatim to Planner. */
export const propGenerateType='prop.generate',propGenerateSchema='ngs.prop-generate.v1';
export const propGenerateInvalid='NGS_PROP_GENERATE_PAYLOAD_INVALID',propGenerateUnavailable='PROP_GENERATE_RUNTIME_UNAVAILABLE';
export const propGenerateMotionUnavailable='PROP_GENERATE_MOTION_UNAVAILABLE';
export const exactProp=(value,keys)=>value!==null&&typeof value==='object'&&!Array.isArray(value)&&
 Object.keys(value).length===keys.length&&keys.every(k=>Object.hasOwn(value,k));
export const propHash=v=>typeof v==='string'&&v.length===64&&/^[0-9a-f]{64}$/u.test(v);
const slug=v=>typeof v==='string'&&v.trim()===v&&/^[a-z][a-z0-9_-]{0,127}$/u.test(v);
const fields=['schemaVersion','projectId','propId','intent','designContextSha256'];
export function validatePropGenerate(v,project){
 if(!(exactProp(v,fields)||exactProp(v,[...fields,'motion']))||v.schemaVersion!==propGenerateSchema||
    project!=='binari'||v.projectId!==project||!slug(v.propId)||!propHash(v.designContextSha256)||
    Object.hasOwn(v,'motion')&&!['still','subtle'].includes(v.motion)||typeof v.intent!=='string'||
    !v.intent.isWellFormed()||!v.intent.length||v.intent.trim()!==v.intent||Buffer.byteLength(v.intent)>4000||
    /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/u.test(v.intent))throw Error(propGenerateInvalid);
 return JSON.stringify(Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])));
}
export const propGeneratePayloadSchema={type:'object',properties:{
 schemaVersion:{const:propGenerateSchema},projectId:{const:'binari'},propId:{type:'string',pattern:'^[a-z][a-z0-9_-]{0,127}$'},
 intent:{type:'string',minLength:1,maxLength:4000},designContextSha256:{type:'string',pattern:'^[a-f0-9]{64}$'},
 motion:{enum:['still','subtle']},
},required:fields,additionalProperties:false};
