/** RS independently reads exactly one typed Space queue entry from the approved Site. */
import {closeSync,constants,fstatSync,lstatSync,openSync,readFileSync,realpathSync} from 'node:fs';
import {dirname,isAbsolute,resolve,sep} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spaceQueueIdPattern} from './space-intake.mjs';

export const spaceMachineUrl='https://vinus-npcgen.park-venus0518.chatgpt.site/api/space-machine-mcp';
const protocol='2026-07-28',maxReply=256*1024,maxCredential=4096;
const sourceRoot=realpathSync(fileURLToPath(new URL('../',import.meta.url)));
const queueIdPattern=new RegExp(spaceQueueIdPattern);
const configError=()=>Error('REQUEST_SPACE_RELAY_CONFIGURATION_INVALID');
const unavailable=()=>Error('REQUEST_SPACE_RELAY_UNAVAILABLE');
const denied=()=>Error('REQUEST_SPACE_NOT_VERIFIED');
const keys=(value,expected)=>!!value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).length===expected.length&&expected.every(k=>Object.hasOwn(value,k));
const validToken=v=>typeof v==='string'&&v.length>=32&&v.length<=512&&/^[A-Za-z0-9._~+/=-]+$/.test(v)&&!/\s/.test(v);
const validCredentials=v=>keys(v,['token','platformToken'])&&typeof v.token==='string'&&v.token.length===64&&/^[a-f0-9]+$/.test(v.token)&&validToken(v.platformToken)&&v.token!==v.platformToken;

export function readSpaceMachineCredentials(path){
 try{
  if(typeof path!=='string'||!isAbsolute(path)||resolve(path)!==path||path.startsWith(sourceRoot+sep)||
     realpathSync(path)!==path)throw configError();
  const parent=lstatSync(dirname(path));
  if(!parent.isDirectory()||parent.uid!==process.getuid()||parent.mode&0o022)throw configError();
  const fd=openSync(path,constants.O_RDONLY|constants.O_CLOEXEC|constants.O_NOFOLLOW|constants.O_NONBLOCK);
  try{
   const st=fstatSync(fd),current=lstatSync(path);
   if(!st.isFile()||st.uid!==process.getuid()||st.nlink!==1||(st.mode&0o7777)!==0o600||
      st.size<2||st.size>maxCredential||st.ino!==current.ino||st.dev!==current.dev)throw configError();
   const raw=readFileSync(fd),after=fstatSync(fd);
   if(raw.length!==st.size||after.mtimeMs!==st.mtimeMs||after.ctimeMs!==st.ctimeMs)throw configError();
   const v=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(raw));
   if(!validCredentials(v))throw configError();
   return v;
  }finally{closeSync(fd);}
 }catch{throw configError();}
}

async function readMachineResponse(response){
 if(!/^application\/json(?:\s*;|$)/i.test(response?.headers?.get('content-type')??''))throw unavailable();
 const length=response.headers.get('content-length');
 if(length!==null&&(!/^\d+$/.test(length)||Number(length)>maxReply))throw unavailable();
 const reader=response.body?.getReader();if(!reader)throw unavailable();
 const decoder=new TextDecoder('utf-8',{fatal:true});
 let size=0,raw='';
 try{
  for(;;){
   const {value,done}=await reader.read();if(done)break;
   size+=value.byteLength;if(size>maxReply)throw unavailable();
   raw+=decoder.decode(value,{stream:true});
  }
  return JSON.parse(raw+decoder.decode());
 }catch{throw unavailable();}
 finally{void reader.cancel().catch(()=>{});}
}

export function createSpaceMachineReader(credentials,{send=fetch}={}){
 if(!validCredentials(credentials)||typeof send!=='function')throw configError();
 const {token,platformToken}=credentials;
 let sequence=0;
 return async function readSpaceRequest(queueId){
  if(typeof queueId!=='string'||!queueIdPattern.test(queueId)||/\s/.test(queueId))throw denied();
  const id=++sequence;
  let response;
  try{
   response=await send(spaceMachineUrl,{
    method:'POST',redirect:'error',signal:AbortSignal.timeout(10_000),
    headers:{'Content-Type':'application/json','Accept':'application/json, text/event-stream',
     'MCP-Protocol-Version':protocol,'X-NGS-Machine-Token':token,
     'OAI-Sites-Authorization':'Bearer '+platformToken},
    body:JSON.stringify({jsonrpc:'2.0',id,method:'tools/call',params:{name:'space_requests_get',arguments:{queueId}}}),
   });
  }catch{throw unavailable();}
  if(response?.status!==200){
   void response?.body?.cancel().catch(()=>{});
   throw [401,403,404].includes(response?.status)?denied():unavailable();
  }
  const v=await readMachineResponse(response);
  if(!keys(v,['jsonrpc','id','result'])||v.jsonrpc!=='2.0'||v.id!==id)throw unavailable();
  if(v.result?.isError!==false)throw denied();
  return v.result.structuredContent;
 };
}

export function configureSpaceMachineReader(env){
 const enabled=env.NGS_SPACE_REQUEST_RELAY_ENABLED;
 if(enabled===undefined||enabled==='0')return undefined;
 if(enabled!=='1')throw configError();
 return createSpaceMachineReader(readSpaceMachineCredentials(env.NGS_SPACE_REQUEST_CREDENTIAL_FILE));
}
