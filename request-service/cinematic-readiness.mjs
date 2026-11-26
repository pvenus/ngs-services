import {extraCinematicRequests} from './cinematic-envelope.mjs';
const origin='http://127.0.0.1:8030/v1/cinematics/ownership/workflow';
const sequenceOrigin=origin+'-sequence',playbackOrigin=origin+'-playback';
async function probe(path,body,fetcher,base=origin){
 const result=await fetcher(base+path,{redirect:'error',cache:'no-store',signal:AbortSignal.timeout(3000),
  ...(body!==undefined?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
 if(result.status!==200||!/^application\/json(?:;|$)/iu.test(result.headers.get('content-type')??'')){
  await result.body?.cancel();
  return result.status===404?null:result.status===409?'stale':false;
 }
 let bytes=0;const parts=[];
 for await(const p of result.body){bytes+=p.length;if(bytes>1024)return false;parts.push(p);}
 return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(parts,bytes)));
}
export async function cinematicReady(fetcher=fetch){
 try{
  const v=await probe('',undefined,fetcher);
  return v&&Object.keys(v).sort().join(',')==='enabled,schemaVersion'&&v.enabled===true&&v.schemaVersion==='workflow.cinematic-write.v1';
 }catch{return false;}
}
export async function cinematicSourceReady(sourceSha256,fetcher=fetch){
 try{
  const v=await probe('/source-status',{sourceSha256},fetcher);
  if(v===null)return null;
  return v&&Object.keys(v).sort().join(',')==='ok,sourceSha256'&&v.ok===true&&v.sourceSha256===sourceSha256;
 }catch{return false;}
}

export async function cinematicSequenceReady(fetcher=fetch){
 try{
  const v=await probe('',undefined,fetcher,sequenceOrigin);
  return v&&Object.keys(v).sort().join(',')==='enabled,schemaVersion'&&v.enabled===true&&v.schemaVersion==='workflow.cinematic-sequence-write.v1';
 }catch{return false;}
}
export async function cinematicSequenceSourceReady(sourceSha256,fetcher=fetch){
 try{
  const v=await probe('/source-status',{sourceSha256},fetcher,sequenceOrigin);
  if(v===null)return null;
  return v&&Object.keys(v).sort().join(',')==='ok,sourceSha256'&&v.ok===true&&v.sourceSha256===sourceSha256;
 }catch{return false;}
}

export async function cinematicPlaybackReady(fetcher=fetch){
 try{
  const v=await probe('',undefined,fetcher,playbackOrigin);
  return v&&Object.keys(v).sort().join(',')==='enabled,schemaVersion'&&v.enabled===true&&v.schemaVersion==='workflow.cinematic-playback-write.v1';
 }catch{return false;}
}
export async function cinematicPlaybackTargetReady(sequenceId,expectedRevision,fetcher=fetch){
 try{
  const v=await probe('/target-status',{sequenceId,expectedRevision},fetcher,playbackOrigin);
  if(v===null||v==='stale')return v;
  return v&&Object.keys(v).sort().join(',')==='ok,revision,sequenceId'&&v.ok===true&&
   v.sequenceId===sequenceId&&v.revision===expectedRevision;
 }catch{return false;}
}

export async function cinematicOperationReady(type,fetcher=fetch){
 try{
  if(!Object.hasOwn(extraCinematicRequests,type))return false;
  const {route,capability}=extraCinematicRequests[type];
  const v=await probe('',undefined,fetcher,'http://127.0.0.1:8030'+route);
  return v&&Object.keys(v).sort().join(',')==='enabled,schemaVersion'&&v.enabled===true&&v.schemaVersion===capability;
 }catch{return false;}
}

export async function backgroundAnimationReferencesReady({backgroundSha256,references},fetcher=fetch){
 try{
  const v=await probe('/background-status',{backgroundSha256,references},fetcher,sequenceOrigin);
  if(v===null)return null;
  return v&&Object.keys(v).join(',')==='ok'&&v.ok===true;
 }catch{return false;}
}
