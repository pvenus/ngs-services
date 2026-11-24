/** Shared official prop image intent; no executable, path, or provider override. */
export const propImageType='prop.image-generate',propImageSchema='ngs.prop-image-generate.v1';
export const propImageKey='prop_image_generate',propImageInvalid='NGS_PROP_IMAGE_PAYLOAD_INVALID',propImageUnavailable='PROP_IMAGE_RUNTIME_UNAVAILABLE';
const exact=(v,ks)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===ks.length&&ks.every(k=>Object.hasOwn(v,k));
const slug=v=>typeof v==='string'&&v.length<=128&&v.trim()===v&&/^[a-z][a-z0-9_-]*$/u.test(v);
const sha=v=>typeof v==='string'&&v.length===64&&/^[a-f0-9]{64}$/u.test(v);
export const propImageRoot='media-artifacts/assets/';
export const propImageRef=(s)=>{if(!sha(s))throw Error(propImageInvalid);return propImageRoot+s+'.png';};
export const validPropImageRef=v=>typeof v==='string'&&v.startsWith(propImageRoot)&&v.endsWith('.png')&&sha(v.slice(propImageRoot.length,-4));
export function validatePropImage(v,project){
 if(!exact(v,['schemaVersion','projectId','propId','state','lane','intent','designContextSha256'])||
    v.schemaVersion!==propImageSchema||project!=='binari'||v.projectId!==project||!slug(v.propId)||!slug(v.state)||
    !['detail','pixel'].includes(v.lane)||typeof v.intent!=='string'||!v.intent.isWellFormed()||v.intent.trim()!==v.intent||
    v.intent.length===0||Buffer.byteLength(v.intent)>4000||/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/u.test(v.intent)||!sha(v.designContextSha256))
  throw Error(propImageInvalid);
 return JSON.stringify(Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])));
}
const id={type:'string',pattern:'^[a-z][a-z0-9_-]{0,127}$'};
export const propImagePayloadSchema={type:'object',properties:{
 schemaVersion:{const:propImageSchema},projectId:{const:'binari'},propId:id,state:id,lane:{enum:['detail','pixel']},
 intent:{type:'string',minLength:1,maxLength:4000},designContextSha256:{type:'string',pattern:'^[a-f0-9]{64}$'},
},required:['schemaVersion','projectId','propId','state','lane','intent','designContextSha256'],additionalProperties:false};
