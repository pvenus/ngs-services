// Readiness is independent of RS, work availability, and successful OPS delivery.
const endpoint='http://127.0.0.1:8090/api/service-heartbeat';
export function startPlannerHeartbeat({revision,send=globalThis.fetch,every=setInterval,cancel=clearInterval}={}){
 const controller=new AbortController();
 const payload=JSON.stringify({serverName:'workflow-planner',
  ...(typeof revision==='string'&&/^[a-f0-9]{40}$/.test(revision)?{commitSha:revision}:{})});
 let sending=false;
 async function tick(){
  if(sending||controller.signal.aborted)return;
  sending=true;
  try{
   const response=await send(endpoint,{
    method:'POST',redirect:'error',headers:{'Content-Type':'application/json'},body:payload,
    signal:AbortSignal.any([controller.signal,AbortSignal.timeout(5000)]),
   });
   await response.body?.cancel();
  }catch{/* The local status observer cannot block Planner. */}
  finally{sending=false;}
 }
 const timer=every(tick,60_000);
 timer.unref?.();
 void tick();
 return ()=>{
  if(controller.signal.aborted)return;
  cancel(timer);
  controller.abort();
 };
}
