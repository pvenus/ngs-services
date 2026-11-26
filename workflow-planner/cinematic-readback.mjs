import {validCinematicPlayback,extraCinematicRequests,cinematicSourceType,backgroundSourceType,backgroundPixelType,backgroundAnimationType,cinematicArchiveType,propAnimationRemoveType} from './cinematic-envelope.mjs';
import {canonical} from './envelope.mjs';

export async function readCinematicPublication({officialRequestId,family,group}){
 const route=family==='cinematic-sequence'?'workflow-sequence':family==='cinematic-playback'?'workflow-playback':'workflow';
 const path=family===backgroundAnimationType?'/v1/cinematics/ownership/workflow-sequence':Object.hasOwn(extraCinematicRequests,family)?extraCinematicRequests[family].route:'/v1/cinematics/ownership/'+route;
 const r=await fetch('http://127.0.0.1:8030'+path+'/result',{
  method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(family===backgroundAnimationType?{officialRequestId,group}:{officialRequestId}),
  signal:AbortSignal.timeout(family===backgroundAnimationType?20_000:5000),redirect:'error',cache:'no-store',
 });
 if(r.status!==200||!/^application\/json(?:;|$)/iu.test(r.headers.get('content-type')??'')){
  await r.body?.cancel();throw Error('CINEMATIC_READBACK_UNAVAILABLE');
 }
 let size=0;const chunks=[];
 for await(const chunk of r.body){
  size+=chunk.length;if(size>64*1024)throw Error('CINEMATIC_READBACK_LIMIT');chunks.push(chunk);
 }
 return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(chunks,size)));
}

export function verifyCinematicReadback(candidate,value){
 return value&&Object.keys(value).sort().join(',')==='manifest,ok,resultRef'&&value.ok===true&&
  value.resultRef===candidate.resultRef&&verifyCinematicManifest(candidate,value.manifest);
}
function verifyCinematicManifest(candidate,m){
 return m&&Object.keys(m).sort().join(',')==='bytes,fps,frameCount,height,mimeType,officialRequestId,projectId,schemaVersion,sha256,sourceSha256,width'&&
  m.schemaVersion==='ngs.cinematic-result.v1'&&m.projectId==='binari'&&m.officialRequestId===candidate.officialRequestId&&
  m.sourceSha256===candidate.sourceSha256&&typeof m.sha256==='string'&&m.sha256.length===64&&/^[a-f0-9]{64}$/u.test(m.sha256)&&
  m.mimeType==='video/mp4'&&m.fps===24&&m.frameCount===45&&Number.isSafeInteger(m.bytes)&&m.bytes>0&&m.bytes<=8*1024*1024&&
  [m.width,m.height].every(n=>Number.isSafeInteger(n)&&n>=128&&n<=512&&n%32===0);
}

export function verifyCinematicSequenceReadback(candidate,value){
 if(!value||Object.keys(value).sort().join(',')!=='manifest,ok,resultRef'||value.ok!==true||value.resultRef!==candidate.resultRef)return false;
 const m=value.manifest;
 const upscale=Object.hasOwn(m??{},'upscale');
 const compact=!!candidate.group&&candidate.playback?.durationFrames===84&&candidate.playback?.speedCurve?.[0]?.rate===1/6;
 const count=compact?8:45,reduced=compact&&candidate.group.variant==='pixel';
 if(compact&&!upscale)return false;
 if(upscale){
  const u=m.upscale;
  if(!candidate.group||!u||Object.keys(u).filter(k=>k!=='pixelCorrection').sort().join(',')!=='model,scale,sourceHeight,sourceWidth'||
   u.model!=='seedvr2-7b-fp16'||u.scale!==2||
   ![u.sourceWidth,u.sourceHeight].every(n=>Number.isInteger(n)&&n>=128&&n<=512&&n%32===0)||
   m.width!==u.sourceWidth*(reduced?1:2)||m.height!==u.sourceHeight*(reduced?1:2))return false;
  if(Object.hasOwn(u,'pixelCorrection')){
   const c=u.pixelCorrection,h=c?.upscaledFramesSha256;
   if(candidate.group.variant!=='pixel'||!c||Object.keys(c).sort().join(',')!==(reduced?'downscale,method,upscaledFramesSha256':'method,upscaledFramesSha256')||
    (reduced&&c.downscale!==2)||c.method!=='reference-palette-v1'||typeof h!=='string'||h.length!==64||!/^[a-f0-9]{64}$/u.test(h))return false;
  }else if(reduced)return false;
 }
 const keys=Object.keys(m??{}).filter(k=>k!=='upscale').sort().join(',');
 return m&&keys===(candidate.group?'frameCount,frames,group,height,masterFps,officialRequestId,playback,projectId,schemaVersion,sourceSha256,width':'frameCount,frames,height,masterFps,officialRequestId,playback,projectId,schemaVersion,sourceSha256,width')&&
  (!candidate.group||canonical(candidate.group)===canonical(m.group))&&
  m.schemaVersion==='ngs.cinematic-sequence-result.v1'&&m.projectId==='binari'&&m.officialRequestId===candidate.officialRequestId&&
  m.sourceSha256===candidate.sourceSha256&&m.frameCount===count&&m.masterFps===24&&
  (upscale||[m.width,m.height].every(n=>Number.isSafeInteger(n)&&n>=128&&n<=512&&n%32===0))&&
  validCinematicPlayback(m.playback)&&canonical(m.playback)===canonical(candidate.playback)&&
  Array.isArray(m.frames)&&m.frames.length===count&&m.frames.every((f,i)=>f&&Object.keys(f).sort().join(',')==='bytes,index,sha256'&&
    f.index===i&&Number.isInteger(f.bytes)&&f.bytes>0&&f.bytes<=4*1024*1024&&typeof f.sha256==='string'&&
    f.sha256.length===64&&/^[a-f0-9]{64}$/u.test(f.sha256))&&m.frames.reduce((n,f)=>n+f.bytes,0)<=(upscale?128:64)*1024*1024;
}

export function verifyCinematicPlaybackReadback(candidate,value){
 if(!value||Object.keys(value).sort().join(',')!=='ok,resultRef,state'||value.ok!==true||value.resultRef!==candidate.resultRef)return false;
 const s=value.state;
 return s&&Object.keys(s).sort().join(',')==='playback,projectId,revision,schemaVersion,sequenceId'&&
  s.schemaVersion==='ngs.cinematic-playback-state.v1'&&s.projectId==='binari'&&s.sequenceId===candidate.sequenceId&&
  s.revision===candidate.expectedRevision+1&&validCinematicPlayback(s.playback)&&canonical(s.playback)===canonical(candidate.playback);
}

export function verifyExtraCinematicReadback(candidate,value){
 if(!value||value.ok!==true||value.resultRef!==candidate.resultRef)return false;
 const keys=Object.keys(value).sort().join(',');
 if([cinematicSourceType,backgroundSourceType,backgroundPixelType].includes(candidate.family)){
  const pixel=candidate.family===backgroundPixelType,background=pixel||candidate.family===backgroundSourceType;
  const m=value.manifest;
  return keys==='manifest,ok,resultRef'&&m&&
   Object.keys(m).sort().join(',')==='bytes,height,officialRequestId,projectId,schemaVersion,sha256,title,width'&&
   m.schemaVersion===(background?'ngs.background-source-result.v1':'ngs.cinematic-source-result.v1')&&m.projectId==='binari'&&m.officialRequestId===candidate.officialRequestId&&
   (pixel?m.title===candidate.title+' · 픽셀 보정':m.title===candidate.title)&&m.width===1536&&m.height===1024&&Number.isInteger(m.bytes)&&m.bytes>0&&m.bytes<=8*1024*1024&&
   typeof m.sha256==='string'&&m.sha256.length===64&&/^[a-f0-9]{64}$/u.test(m.sha256)&&
   candidate.resultRef==='binari/cinematics/'+(background?'backgrounds':'sources')+'/'+m.sha256+'.json';
 }
 if(candidate.family===cinematicArchiveType)return keys==='ok,resultRef,sequenceIds'&&
    Array.isArray(value.sequenceIds)&&canonical(value.sequenceIds)===canonical(candidate.sequenceIds);
 if(candidate.family===propAnimationRemoveType)return keys==='motionIds,ok,propId,resultRef,revision'&&
    value.propId===candidate.propId&&value.revision===candidate.source.revision+1&&Array.isArray(value.motionIds)&&
    canonical(value.motionIds)===canonical(candidate.motionIds);
 return false;
}
