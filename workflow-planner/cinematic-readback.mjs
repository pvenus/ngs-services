import {validCinematicPlayback} from './cinematic-envelope.mjs';
import {canonical} from './envelope.mjs';

export async function readCinematicPublication({officialRequestId,family}){
 const route=family==='cinematic-sequence'?'workflow-sequence':family==='cinematic-playback'?'workflow-playback':'workflow';
 const r=await fetch('http://127.0.0.1:8030/v1/cinematics/ownership/'+route+'/result',{
  method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({officialRequestId}),
  signal:AbortSignal.timeout(5000),redirect:'error',cache:'no-store',
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
 return m&&Object.keys(m).sort().join(',')==='frameCount,frames,height,masterFps,officialRequestId,playback,projectId,schemaVersion,sourceSha256,width'&&
  m.schemaVersion==='ngs.cinematic-sequence-result.v1'&&m.projectId==='binari'&&m.officialRequestId===candidate.officialRequestId&&
  m.sourceSha256===candidate.sourceSha256&&m.frameCount===45&&m.masterFps===24&&
  [m.width,m.height].every(n=>Number.isSafeInteger(n)&&n>=128&&n<=512&&n%32===0)&&
  validCinematicPlayback(m.playback)&&canonical(m.playback)===canonical(candidate.playback)&&
  Array.isArray(m.frames)&&m.frames.length===45&&m.frames.every((f,i)=>f&&Object.keys(f).sort().join(',')==='bytes,index,sha256'&&
    f.index===i&&Number.isInteger(f.bytes)&&f.bytes>0&&f.bytes<=4*1024*1024&&typeof f.sha256==='string'&&
    f.sha256.length===64&&/^[a-f0-9]{64}$/u.test(f.sha256))&&m.frames.reduce((n,f)=>n+f.bytes,0)<=64*1024*1024;
}

export function verifyCinematicPlaybackReadback(candidate,value){
 if(!value||Object.keys(value).sort().join(',')!=='ok,resultRef,state'||value.ok!==true||value.resultRef!==candidate.resultRef)return false;
 const s=value.state;
 return s&&Object.keys(s).sort().join(',')==='playback,projectId,revision,schemaVersion,sequenceId'&&
  s.schemaVersion==='ngs.cinematic-playback-state.v1'&&s.projectId==='binari'&&s.sequenceId===candidate.sequenceId&&
  s.revision===candidate.expectedRevision+1&&validCinematicPlayback(s.playback)&&canonical(s.playback)===canonical(candidate.playback);
}
