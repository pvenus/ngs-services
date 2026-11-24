import {createServer} from 'node:http';
import {pathToFileURL} from 'node:url';
import {openOfficialInbox,validateInboxPath} from './official-inbox.mjs';
import {requestMcp} from './request-mcp.mjs';
import {startServiceHeartbeat} from './service-heartbeat.mjs';
import {configureSpaceMachineReader} from './space-machine-reader.mjs';
import {propPackageReady,propAnimationReady} from './prop-package-readiness.mjs';
import {propImageReady} from './prop-image-readiness.mjs';

import {cinematicReady,cinematicSourceReady,cinematicSequenceReady,cinematicSequenceSourceReady,cinematicPlaybackReady,cinematicPlaybackTargetReady,cinematicOperationReady} from './cinematic-readiness.mjs';

export const host='127.0.0.1',port=8770;
export function configuration(env){
 const directory=validateInboxPath(env.REQUEST_OFFICIAL_INBOX_DIR);
 const readSpaceRequest=configureSpaceMachineReader(env);
 return {directory,...(readSpaceRequest?{readSpaceRequest}:{})};
}
export function createRequestService({directory,readSpaceRequest,propImageEnabled=false,propImageReadiness,propGenerateEnabled=false,propGenerateMotionEnabled=false,propAnimationEnabled=false,propAnimationReadiness,propMutationsEnabled=false,propPackageReadiness,cinematicEnabled=false,cinematicReadiness,cinematicSourceReadiness,cinematicLegacyAdmission,cinematicSequenceReadiness,cinematicSequenceSourceReadiness,cinematicPlaybackReadiness,cinematicPlaybackTargetReadiness,cinematicOperationReadiness}){
 if(readSpaceRequest!==undefined&&typeof readSpaceRequest!=='function')throw Error('REQUEST_SPACE_INVALID');
 const inbox=openOfficialInbox(directory,{readSpaceRequest,propImageEnabled,propGenerateEnabled,propGenerateMotionEnabled,propAnimationEnabled,propMutationsEnabled,cinematicEnabled});
 const server=createServer({headersTimeout:15000,requestTimeout:15000},(req,res)=>{
  void requestMcp(req,res,inbox,{readSpaceRequest,propImageEnabled,propImageReadiness,propGenerateEnabled,propGenerateMotionEnabled,propAnimationEnabled,propAnimationReadiness,propMutationsEnabled,propPackageReadiness,cinematicEnabled,cinematicReadiness,cinematicSourceReadiness,cinematicLegacyAdmission,cinematicSequenceReadiness,cinematicSequenceSourceReadiness,cinematicPlaybackReadiness,cinematicPlaybackTargetReadiness,cinematicOperationReadiness}).catch(()=>{
   if(res.headersSent)return res.destroy();
   const raw=JSON.stringify({error:'OFFICIAL_REQUEST_REGISTER_FAILED'});
   res.writeHead(503,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'}).end(raw);
  });
 });
 server.once('close',()=>inbox.close());
 return server;
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
 try{
  const server=createRequestService({...configuration(process.env),propImageEnabled:true,propImageReadiness:propImageReady,
   propGenerateEnabled:true,propMutationsEnabled:true,propAnimationEnabled:true,
   cinematicEnabled:true,cinematicLegacyAdmission:false,cinematicSequenceReadiness:cinematicSequenceReady,cinematicSequenceSourceReadiness:cinematicSequenceSourceReady,
   cinematicPlaybackReadiness:cinematicPlaybackReady,cinematicPlaybackTargetReadiness:cinematicPlaybackTargetReady,cinematicOperationReadiness:cinematicOperationReady,
   propAnimationReadiness:propAnimationReady,propPackageReadiness:propPackageReady});
  server.on('error',err=>{
   console.error(err.code==='EADDRINUSE'?'REQUEST_SERVICE_PORT_IN_USE':'REQUEST_SERVICE_START_FAILED');
   server.close();
   process.exitCode=1;
  });
  let stopHeartbeat=()=>{};
  server.listen(port,host,()=>{
   console.log('REQUEST_SERVICE_MCP_READY http://127.0.0.1:8770/mcp');
   stopHeartbeat=startServiceHeartbeat();
  });
  server.once('close',()=>stopHeartbeat());
  for(const signal of ['SIGTERM','SIGINT'])process.once(signal,()=>{stopHeartbeat();server.close();});
 }catch(error){
  console.error(['OFFICIAL_REQUEST_INBOX_PRIVATE_REQUIRED','OFFICIAL_REQUEST_SCHEMA_UNSUPPORTED','REQUEST_SPACE_RELAY_CONFIGURATION_INVALID'].includes(error?.message)?error.message:'REQUEST_SERVICE_START_FAILED');
  process.exitCode=1;
 }
}
