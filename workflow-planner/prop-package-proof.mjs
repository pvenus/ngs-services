/** Boundary contract for the trusted PDS/Filex prop publisher, never a worker-local file. */
import {canonical,sha} from './envelope.mjs';
import {exactProp,propHash,propGenerateType,propRegenerateType,propAnimationType,propScaleType,validPropScalePercents,propValidator,propPresentationProfiles,pixelSourcePresentationProfile} from './prop-generate-envelope.mjs';
import {checkPropPublishRows,propKeys,propRef,propTaskId,verifyPropScaleRefs} from './prop-generate-plan.mjs';
const fail=()=>Error('PROP_PACKAGE_PROOF_INVALID');
const need=x=>{if(!x)throw fail();};
const positive=(n,max)=>Number.isSafeInteger(n)&&n>0&&n<=max;
const matches=(a,b)=>canonical(a)===canonical(b);
function binding(run){
 const {officialRequestId,payload,payloadSha256}=run;
 const {requestType,projectId}=run;
 need(Object.hasOwn(propValidator,requestType));
 const body=propValidator[requestType](payload,projectId);
 need(sha(body)===payloadSha256);
 return {officialRequestId,projectId,propId:payload.propId,requestSha256:payloadSha256,
  ...(payload.scalePercents?{scalePercents:payload.scalePercents}:{}),
  ...(payload.presentationProfile?{presentationProfile:payload.presentationProfile}:{}),
  ...(requestType===propGenerateType?{designContextSha256:payload.designContextSha256,motion:payload.motion==='subtle'?'subtle':'still'}:
   requestType===propRegenerateType?{operation:'regenerate',source:payload.source,designContextSha256:payload.designContextSha256,motion:payload.motion==='subtle'?'subtle':'still'}:
   requestType===propScaleType?{operation:'append-scales',source:payload.source}:
   {operation:'append-animation',source:payload.source,animationId:payload.motionId})};
}
function checkMedia(v,motion,lane,small=false){
 const max=lane==='detail'?2048:512,min=small?(lane==='detail'?1:2):12;
 need(exactProp(v,['assetId','sha256','bytes','width','height','mediaType','frameDurationsMs'])&&
  propHash(v.sha256)&&v.assetId===v.sha256+'.png'&&positive(v.bytes,16*1024*1024)&&v.bytes>=57&&
  positive(v.width,max)&&v.width>=min&&positive(v.height,max)&&v.height>=min&&Array.isArray(v.frameDurationsMs));
 if(motion==='still')need(v.mediaType==='image/png'&&v.frameDurationsMs.length===0);
 else need(v.mediaType==='image/apng'&&v.frameDurationsMs.length>=2&&v.frameDurationsMs.length<=96&&
  v.frameDurationsMs.every(d=>positive(d,1000)&&d>=10)&&
  v.frameDurationsMs.reduce((a,b)=>a+b,0)>=100&&v.frameDurationsMs.reduce((a,b)=>a+b,0)<=10000);
 need(v.width*v.height*Math.max(1,v.frameDurationsMs.length)<=30_000_000);
}
function checkRegion(v,action,media){
 if(v===null)return;
 need(exactProp(v,['action','x','y','width','height'])&&v.action===action&&Number.isSafeInteger(v.x)&&v.x>=0&&
  Number.isSafeInteger(v.y)&&v.y>=0&&positive(v.width,media.width)&&positive(v.height,media.height)&&
  v.x+v.width<=media.width&&v.y+v.height<=media.height);
}
function checkPair(media,motion,members,detailKey,pixelKey,small=false){
 const spirit=motion==='still'&&exactProp(media,['detail','pixel','spirit']);
 need(spirit||exactProp(media,['detail','pixel']));
 const {detail,pixel}=media;
 checkMedia(detail,motion,'detail');checkMedia(pixel,motion,'pixel',small||spirit);
 if(spirit){
  checkMedia(media.spirit,'still','spirit',true);
  for(const lane of [pixel,media.spirit])for(const d of ['width','height'])need(lane[d]===Math.round(detail[d]/5));
 }
 need(pixel.width<=detail.width&&pixel.height<=detail.height&&pixel.width*pixel.height<detail.width*detail.height&&
  Math.abs(pixel.width*detail.height-pixel.height*detail.width)<=Math.max(detail.width,detail.height));
 need(matches(detail.frameDurationsMs,pixel.frameDurationsMs));
 if(members)need(members.some(x=>x.key===detailKey&&x.ref===propRef(detailKey,detail.sha256))&&
  members.some(x=>x.key===pixelKey&&x.ref===propRef(pixelKey,pixel.sha256))&&
  (!spirit||members.some(x=>x.key===propKeys.spirit&&x.ref===propRef(propKeys.spirit,media.spirit.sha256))));
}
const cleanTitle=s=>typeof s==='string'&&s.isWellFormed()&&s.trim()===s&&s.length>0&&
 [...s].length<=160&&!/[\p{Cc}\u2028\u2029]/u.test(s);
const clipId=s=>typeof s==='string'&&s.length>=2&&s.length<=80&&s.trim()===s&&/^[a-z0-9]+(?:[-._][a-z0-9]+)*$/u.test(s);
const validId=s=>typeof s==='string'&&s.length===81&&/^request\.official\.[a-f0-9]{64}$/u.test(s);
function checkClip(clip,base,members){
 need(exactProp(clip,['id','title','officialRequestId','requestSha256','media'])&&clipId(clip.id)&&
  cleanTitle(clip.title)&&validId(clip.officialRequestId)&&propHash(clip.requestSha256));
 checkPair(clip.media,'subtle',members,propKeys.animDetail,propKeys.animPixel,Object.hasOwn(base,'spirit'));
 for(const lane of ['detail','pixel'])need(clip.media[lane].width===base[lane].width&&clip.media[lane].height===base[lane].height);
}
function checkGeneralManifest(m){
 need(exactProp(m,['schemaVersion','officialRequestId','projectId','propId','requestSha256','designContextSha256',
  'revision','title','media','interaction','parentSha256','animations',...(Object.hasOwn(m??{},'scaleVariants')?['scaleVariants']:[]),...(Object.hasOwn(m??{},'scaleSourcePixel')?['scaleSourcePixel']:[])])&&m.schemaVersion==='ngs.prop-package.v1'&&
  m.projectId==='binari'&&clipId(m.propId)&&validId(m.officialRequestId)&&propHash(m.requestSha256)&&propHash(m.designContextSha256)&&
  positive(m.revision,Number.MAX_SAFE_INTEGER-1)&&(m.revision===1?m.parentSha256===null:propHash(m.parentSha256))&&cleanTitle(m.title)&&
  exactProp(m.interaction,['pixelOpen','detailTrigger'])&&Array.isArray(m.animations)&&m.animations.length<=1024);
 checkPair(m.media,'still');
 if(Object.hasOwn(m,'scaleSourcePixel'))checkMedia(m.scaleSourcePixel,'still','detail',true);
 if(Object.hasOwn(m,'scaleVariants')){
  need(Array.isArray(m.scaleVariants)&&validPropScalePercents(m.scaleVariants.map(v=>v?.scalePercent)));
  let last=0;
  for(const v of m.scaleVariants){
   need(exactProp(v,['scalePercent','pixel','spirit'])&&v.scalePercent>last);
   last=v.scalePercent;
   for(const lane of ['pixel','spirit']){
    checkMedia(v[lane],'still','detail',true);
    for(const d of ['width','height'])need(v[lane][d]===Math.max(1,Math.round(m.media.detail[d]*last/100)));
   }
  }
 }
 checkRegion(m.interaction.pixelOpen,'open',m.media.pixel);
 checkRegion(m.interaction.detailTrigger,'activate',m.media.detail);
 const ids=new Set();
 for(const clip of m.animations){checkClip(clip,m.media);need(!ids.has(clip.id));ids.add(clip.id);}
}
function checkManifest(m,scope,members,prior){
 checkGeneralManifest(m);
 need(verifyPropScaleRefs(m,members));
 need(['officialRequestId','projectId','propId','requestSha256'].every(key=>scope[key]===m[key]));
 if(!scope.operation||scope.operation==='regenerate')
  need(Object.hasOwn(m.media,'spirit')===propPresentationProfiles.includes(scope.presentationProfile));
 if(!scope.operation){
  need(prior===undefined&&m.designContextSha256===scope.designContextSha256&&m.revision===1&&m.parentSha256===null&&
   m.animations.length===(scope.motion==='subtle'?1:0));
  checkPair(m.media,'still',members,propKeys.detail,propKeys.pixel);
  need(matches(m.scaleVariants?.map(x=>x.scalePercent)??[],[...(scope.scalePercents??[])].sort((a,b)=>a-b)));
  if(scope.scalePercents)need(m.scaleSourcePixel);
  if(m.scaleSourcePixel)need(scope.presentationProfile===pixelSourcePresentationProfile&&scope.scalePercents&&
   members.some(row=>row.key===propKeys.pixelDetail&&row.ref===propRef(propKeys.pixelDetail,m.scaleSourcePixel.sha256)));
  if(scope.motion==='subtle'){
   const [clip]=m.animations;
   need(clip.officialRequestId===scope.officialRequestId&&clip.requestSha256===scope.requestSha256&&clip.id==='initial');
   checkClip(clip,m.media,members);
  }
 }else{
  checkGeneralManifest(prior);
  need(prior.projectId===m.projectId&&prior.propId===m.propId&&prior.revision===scope.source.revision&&
   sha(canonical(prior))===scope.source.sha256&&m.revision===prior.revision+1&&m.parentSha256===scope.source.sha256&&
   prior.officialRequestId!==m.officialRequestId);
  if(scope.operation==='regenerate'){
   need(!m.scaleVariants&&!m.scaleSourcePixel);
   need(m.designContextSha256===scope.designContextSha256&&m.animations.length===(scope.motion==='subtle'?1:0));
   checkPair(m.media,'still',members,propKeys.detail,propKeys.pixel);
   if(scope.motion==='subtle'){
    const [clip]=m.animations;
    need(clip.officialRequestId===scope.officialRequestId&&clip.requestSha256===scope.requestSha256&&clip.id==='initial');
    checkClip(clip,m.media,members);
   }
  }else if(scope.operation==='append-scales'){
   for(const field of ['media','designContextSha256','title','interaction','animations','scaleSourcePixel'])need(matches(m[field],prior[field]));
   const old=new Map((prior.scaleVariants??[]).map(x=>[x.scalePercent,x]));
   const next=new Map((m.scaleVariants??[]).map(x=>[x.scalePercent,x]));
   need(scope.scalePercents.every(n=>!old.has(n)&&next.has(n))&&next.size===old.size+scope.scalePercents.length&&
    [...old].every(([n,v])=>matches(v,next.get(n))));
  }else{
   need(matches(m.scaleVariants,prior.scaleVariants)&&matches(m.scaleSourcePixel,prior.scaleSourcePixel));
   need(scope.operation==='append-animation' &&m.designContextSha256===prior.designContextSha256&&m.title===prior.title&&
    matches(m.media,prior.media)&&matches(m.interaction,prior.interaction)&&m.animations.length===prior.animations.length+1&&
    matches(m.animations.slice(0,-1),prior.animations));
   const clip=m.animations.at(-1);
   need(clip.id===scope.animationId&&clip.officialRequestId===scope.officialRequestId&&clip.requestSha256===scope.requestSha256);
   checkClip(clip,m.media,members);
  }
 }
 if(scope.presentationProfile===pixelSourcePresentationProfile)
  need([propKeys.pixelSource,propKeys.pixelDetail].every(key=>members.some(row=>row.key===key)));
 const text=canonical(m);need(Buffer.byteLength(text)<=512*1024);return sha(text);
}

/** Run under the same fixed PDS claim/commit fence used for the authoritative read/write.
 * The caller must resolve/check member bytes through PDS before committing a Filex receipt.
 * This prepares a candidate, never publishes or allocates a version by itself. */
export function preparePropPublication(run,rows,claim,manifest,{prior,head}={}){
 const scope=binding(run),taskId=propTaskId(scope.officialRequestId,propKeys.publish);
 need(run.state==='planned'&&run.reason===null);
 // This witnessed head is also the mandatory compare-and-swap expectation for the writer.
 if(scope.operation)need(exactProp(head,['revision','sha256'])&&matches(head,scope.source));
 else need(head===null);
 need(exactProp(claim,['officialRequestId','projectId','taskId','payloadSha256','claimStamp'])&&
  claim.officialRequestId===scope.officialRequestId&&claim.projectId===scope.projectId&&
  claim.payloadSha256===scope.requestSha256&&claim.taskId===taskId&&
  Number.isSafeInteger(claim.claimStamp)&&claim.claimStamp>0&&
  rows.find(r=>r.task_id===taskId)?.updated_at===claim.claimStamp);
 const members=checkPropPublishRows(run,rows);
 need(members);
 const manifestSha256=checkManifest(manifest,scope,members,prior);
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
  if(!candidate||!exactProp(proof,candidate.scope.operation?['manifest','receipt','prior']:['manifest','receipt']))return false;
  const {manifest,receipt,prior}=proof;
  const digest=checkManifest(manifest,candidate.scope,candidate.members,prior);
  return digest===candidate.manifestSha256&&candidate.resultRef===propRef(propKeys.publish,digest)&&
   exactProp(receipt,['schemaVersion','authority','status','scope','members','resultRef','manifestSha256'])&&
   receipt.schemaVersion==='ngs.prop-package-publication-receipt.v1'&&receipt.authority==='filex'&&receipt.status==='committed'&&
   matches(receipt.scope,candidate.scope)&&matches(receipt.members,candidate.members)&&
   receipt.resultRef===candidate.resultRef&&receipt.manifestSha256===digest;
 }catch{return false;}
}
