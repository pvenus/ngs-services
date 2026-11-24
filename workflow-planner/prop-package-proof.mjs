/** Boundary contract for the trusted PDS/Filex prop publisher, never a worker-local file. */
import {canonical,sha} from './envelope.mjs';
import {exactProp,propHash,propGenerateType,validatePropGenerate} from './prop-generate-envelope.mjs';
import {checkPropPublishRows,propKeys,propRef,propTaskId} from './prop-generate-plan.mjs';
const fail=()=>Error('PROP_PACKAGE_PROOF_INVALID');
const need=x=>{if(!x)throw fail();};
const positive=(n,max)=>Number.isSafeInteger(n)&&n>0&&n<=max;
const matches=(a,b)=>canonical(a)===canonical(b);
function binding(run){
 const {officialRequestId,payload,payloadSha256}=run;
 const body=validatePropGenerate(payload,run.projectId);
 need(run.requestType===propGenerateType&&sha(body)===payloadSha256);
 return {officialRequestId,projectId:run.projectId,propId:payload.propId,requestSha256:payloadSha256,
  designContextSha256:payload.designContextSha256,motion:payload.motion==='subtle'?'subtle':'still'};
}
function checkMedia(v,motion){
 need(exactProp(v,['assetId','sha256','bytes','width','height','mediaType','frameDurationsMs'])&&
  propHash(v.sha256)&&v.assetId===v.sha256+'.png'&&positive(v.bytes,32*1024*1024)&&
  positive(v.width,4096)&&positive(v.height,4096)&&Array.isArray(v.frameDurationsMs));
 if(motion==='still')need(v.mediaType==='image/png'&&v.frameDurationsMs.length===0);
 else need(v.mediaType==='image/apng'&&v.frameDurationsMs.length>=2&&v.frameDurationsMs.length<=96&&
  v.frameDurationsMs.every(d=>positive(d,120000)));
}
function checkRegion(v,action,media){
 if(v===null)return;
 need(exactProp(v,['action','x','y','width','height'])&&v.action===action&&Number.isSafeInteger(v.x)&&v.x>=0&&
  Number.isSafeInteger(v.y)&&v.y>=0&&positive(v.width,media.width)&&positive(v.height,media.height)&&
  v.x+v.width<=media.width&&v.y+v.height<=media.height);
}
function checkPair(media,motion,members,detailKey,pixelKey){
 need(exactProp(media,['detail','pixel']));
 const {detail,pixel}=media;
 checkMedia(detail,motion);checkMedia(pixel,motion);
 need(matches(detail.frameDurationsMs,pixel.frameDurationsMs)&&
  members.some(x=>x.key===detailKey&&x.ref===propRef(detailKey,detail.sha256))&&
  members.some(x=>x.key===pixelKey&&x.ref===propRef(pixelKey,pixel.sha256)));
}
function checkManifest(m,scope,members){
 const {motion,...manifestScope}=scope;
 need(exactProp(m,['schemaVersion',...Object.keys(manifestScope),'revision','title','media','interaction','parentSha256','animations'])&&
  m.schemaVersion==='ngs.prop-package.v1'&&Object.keys(manifestScope).every(key=>manifestScope[key]===m[key])&&
  m.revision===1&&m.parentSha256===null&&
  typeof m.title==='string'&&m.title.isWellFormed()&&m.title.trim()===m.title&&m.title.length>0&&
  Buffer.byteLength(m.title)<=480&&!/[\u0000-\u001f\u007f-\u009f]/u.test(m.title)&&
  exactProp(m.interaction,['pixelOpen','detailTrigger'])&&Array.isArray(m.animations)&&m.animations.length===(motion==='subtle'?1:0));
 checkPair(m.media,'still',members,propKeys.detail,propKeys.pixel);
 checkRegion(m.interaction.pixelOpen,'open',m.media.pixel);
 checkRegion(m.interaction.detailTrigger,'activate',m.media.detail);
 if(motion==='subtle'){
  const [clip]=m.animations;
  need(exactProp(clip,['id','title','officialRequestId','requestSha256','media'])&&clip.id==='initial'&&
   typeof clip.title==='string'&&clip.title.trim()===clip.title&&clip.title.isWellFormed()&&clip.title.length>0&&
   Buffer.byteLength(clip.title)<=480&&!/[\u0000-\u001f\u007f-\u009f]/u.test(clip.title)&&
   clip.officialRequestId===scope.officialRequestId&&clip.requestSha256===scope.requestSha256);
  checkPair(clip.media,'subtle',members,propKeys.animDetail,propKeys.animPixel);
 }
 return sha(canonical(m));
}

/** Run under the same fixed PDS claim/commit fence used for the authoritative read/write.
 * The caller must resolve/check member bytes through PDS before committing a Filex receipt.
 * This prepares a candidate, never publishes or allocates a version by itself. */
export function preparePropPublication(run,rows,claim,manifest){
 const scope=binding(run),taskId=propTaskId(scope.officialRequestId,propKeys.publish);
 need(run.state==='planned'&&run.reason===null);
 need(exactProp(claim,['officialRequestId','projectId','taskId','payloadSha256','claimStamp'])&&
  claim.officialRequestId===scope.officialRequestId&&claim.projectId===scope.projectId&&
  claim.payloadSha256===scope.requestSha256&&claim.taskId===taskId&&
  Number.isSafeInteger(claim.claimStamp)&&claim.claimStamp>0&&
  rows.find(r=>r.task_id===taskId)?.updated_at===claim.claimStamp);
 const members=checkPropPublishRows(run,rows);
 need(members);
 const manifestSha256=checkManifest(manifest,scope,members);
 return {scope,members,manifestSha256,resultRef:propRef(propKeys.publish,manifestSha256)};
}

export function propCompletionCandidate(run,rows){
 const members=checkPropPublishRows(run,rows,{published:true});
 if(!members)return null;
 const final=members.at(-1);
 return {scope:binding(run),members:members.slice(0,-1),resultRef:final.ref,manifestSha256:final.ref.slice(-64)};
}

/** Accept only the authoritative committed receipt and exact independently loaded manifest.
 * The caller that supplies this readback must have an authenticated trusted Filex/PDS port.
 * No receipt strings faked by workers are sufficient for official completion. */
export function verifyPropPublicationProof(candidate,proof){
 try{
  if(!candidate||!exactProp(proof,['manifest','receipt']))return false;
  const {manifest,receipt}=proof;
  const digest=checkManifest(manifest,candidate.scope,candidate.members);
  return digest===candidate.manifestSha256&&candidate.resultRef===propRef(propKeys.publish,digest)&&
   exactProp(receipt,['schemaVersion','authority','status','scope','members','resultRef','manifestSha256'])&&
   receipt.schemaVersion==='ngs.prop-package-publication-receipt.v1'&&receipt.authority==='filex'&&receipt.status==='committed'&&
   matches(receipt.scope,candidate.scope)&&matches(receipt.members,candidate.members)&&
   receipt.resultRef===candidate.resultRef&&receipt.manifestSha256===digest;
 }catch{return false;}
}
