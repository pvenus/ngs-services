import assert from 'node:assert/strict';
import test from 'node:test';
import {mkdtemp,realpath,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createRequestService,host} from './request-service.mjs';
import {cinematicSchema,cinematicType,cinematicSourceMissing,cinematicUnavailable} from './cinematic-envelope.mjs';

test('MCP advertises scoped cinematic only when ready and rejects missing digest before recording',async t=>{
 const parent=await mkdtemp(join(await realpath(tmpdir()),'cinematic-rs-'));
 t.after(()=>rm(parent,{recursive:true,force:true}));
 const sourceSha256='a'.repeat(64);let ready=false,exists=false,space=null;
 const server=createRequestService({directory:join(parent,'inbox'),cinematicEnabled:true,readSpaceRequest:async()=>space,
  cinematicReadiness:()=>ready,cinematicSourceReadiness:sha=>sha===sourceSha256&&exists?true:null});
 await new Promise(resolve=>server.listen(0,host,resolve));
 t.after(()=>new Promise(resolve=>server.close(resolve)));
 const url='http://'+host+':'+server.address().port+'/mcp';
 async function rpc(method,params={}){
  const r=await fetch(url,{method:'POST',headers:{Accept:'application/json, text/event-stream','Content-Type':'application/json'},
   body:JSON.stringify({jsonrpc:'2.0',id:1,method,params})});
  return (await r.json()).result;
 }
 const call=(name,args)=>rpc('tools/call',{name,arguments:args});
 const allowed=async()=> (await rpc('tools/list')).tools[0].inputSchema.properties.requestType.enum.includes(cinematicType);
 const packet={sourceId:'local',requestType:cinematicType,idempotencyKey:'fixture-cinematic',payload:{projectId:'binari',
  schemaVersion:cinematicSchema,sourceSha256,motionPrompt:'00–02초 느린 카메라 이동'}};
 assert.equal(await allowed(),false);
 assert.equal((await call('request_submit',packet)).structuredContent.error,cinematicUnavailable);
 ready=true;assert.equal(await allowed(),true);
 assert.equal((await call('request_submit',packet)).structuredContent.error,cinematicSourceMissing);
 exists=true;
 const accepted=await call('request_submit',packet);assert.equal(accepted.isError,false);
 assert.equal(accepted.structuredContent.status,'registered');
 const operator=(await call('request_list',{limit:8})).structuredContent.requests;
 assert.equal(operator[0].targetNpcId,accepted.structuredContent.officialRequestId);
 assert.equal(operator[0].targetName,'시네마틱 영상 생성');
 const listing=await call('request_workflow_list',{limit:8});
 assert.deepEqual(listing.structuredContent.requests.map(x=>x.payload),[packet.payload]);
 space={queueId:'space-fixture',idempotencyKey:'space-fixture',requestType:cinematicType,payload:packet.payload};
 const spaceAccepted=await call('request_submit_space',{queueId:space.queueId});
 assert.equal(spaceAccepted.isError,false);
 assert.notEqual(spaceAccepted.structuredContent.officialRequestId,accepted.structuredContent.officialRequestId);

});
