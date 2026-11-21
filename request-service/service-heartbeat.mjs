/** Optional reporting to the fixed same-Mac OPS endpoint. No API depends on delivery. */
import {request as httpRequest} from 'node:http';

const endpoint='http://127.0.0.1:8090/api/service-heartbeat';
const payload=Buffer.from(JSON.stringify({serverName:'rs'}));
const intervalMs=60_000, deadlineMs=5_000;

async function acknowledgement(response){
 if(response.statusCode!==200){
  response.destroy();
  return false;
 }
 const chunks=[];let size=0;
 try{
  for await(const chunk of response){
   size+=chunk.length;
   if(size>1024){response.destroy();return false;}
   chunks.push(chunk);
  }
  const result=JSON.parse(Buffer.concat(chunks,size).toString('utf8'));
  return result?.ok===true&&Object.keys(result).length===1;
 }catch{return false;}
}

export function postServiceHeartbeat(signal,{request=httpRequest}={}){
 const combined=AbortSignal.any([signal,AbortSignal.timeout(deadlineMs)]);
 return new Promise(resolve=>{
  const req=request(endpoint,{method:'POST',agent:false,signal:combined,headers:{
   'Content-Type':'application/json','Content-Length':String(payload.length),
  }},response=>{void acknowledgement(response).then(resolve);});
  req.once('error',()=>resolve(false));
  req.end(payload);
 });
}

export function startServiceHeartbeat({send=postServiceHeartbeat,every=setInterval,cancel=clearInterval}={}){
 const lifecycle=new AbortController();
 let sending=false;
 async function tick(){
  if(sending||lifecycle.signal.aborted)return;
  sending=true;
  try{await send(lifecycle.signal);}catch{/* OPS unavailability must not affect Request. */}
  finally{sending=false;}
 }
 const timer=every(tick,intervalMs);
 timer.unref?.();
 void tick();
 return ()=>{
  if(lifecycle.signal.aborted)return;
  cancel(timer);
  lifecycle.abort();
 };
}
