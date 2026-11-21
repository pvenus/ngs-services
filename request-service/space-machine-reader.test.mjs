import assert from 'node:assert/strict';
import {test} from 'node:test';
import {chmodSync,mkdtempSync,realpathSync,rmSync,symlinkSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {configuration} from './request-service.mjs';
import {createSpaceMachineReader,readSpaceMachineCredentials,spaceMachineUrl} from './space-machine-reader.mjs';

const credentials={token:'ab'.repeat(32),platformToken:'synthetic-platform-access-fixture-key'};
const base={REQUEST_OFFICIAL_INBOX_DIR:'/tmp/existing-fixture-inbox'};
test('launcher default stays local; explicit opt-in only accepts a private owned regular credential file',t=>{
 const home=mkdtempSync(join(realpathSync(tmpdir()),'space-reader-'));
 t.after(()=>rmSync(home,{recursive:true,force:true}));
 const path=join(home,'credentials.json');
 writeFileSync(path,JSON.stringify(credentials),{mode:0o600});
 const env={...base,NGS_SPACE_REQUEST_RELAY_ENABLED:'1',NGS_SPACE_REQUEST_CREDENTIAL_FILE:path};
 assert.deepEqual(configuration({...env,NGS_SPACE_REQUEST_RELAY_ENABLED:undefined}),{directory:base.REQUEST_OFFICIAL_INBOX_DIR});
 assert.deepEqual(configuration({...env,NGS_SPACE_REQUEST_RELAY_ENABLED:'0'}),{directory:base.REQUEST_OFFICIAL_INBOX_DIR});
 assert.throws(()=>configuration({...env,NGS_SPACE_REQUEST_RELAY_ENABLED:'yes'}),/CONFIGURATION_INVALID/);
 assert.deepEqual(readSpaceMachineCredentials(path),credentials);
 assert.equal(typeof configuration(env).readSpaceRequest,'function');
 const link=join(home,'link');symlinkSync(path,link);
 assert.throws(()=>readSpaceMachineCredentials(link),/CONFIGURATION_INVALID/);
 chmodSync(path,0o644);
 assert.throws(()=>configuration(env),/CONFIGURATION_INVALID/);
});

test('owner Site read uses fixed HTTPS, exact headers and queue lookup only',async()=>{
 const request={queueId:'space.q1',requestType:'audio.generate',idempotencyKey:'space.q1',payload:{schemaVersion:'fixture'}};
 const send=async(url,options)=>{
  assert.equal(url,spaceMachineUrl);
  assert.equal(options.redirect,'error');
  assert.equal(options.method,'POST');
  assert.equal(options.headers['X-NGS-Machine-Token'],credentials.token);
  assert.equal(options.headers['OAI-Sites-Authorization'],'Bearer '+credentials.platformToken);
  assert.equal(options.headers['MCP-Protocol-Version'],'2026-07-28');
  const body=JSON.parse(options.body);
  assert.deepEqual(body,{jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'space_requests_get',arguments:{queueId:request.queueId}}});
  return Response.json({jsonrpc:'2.0',id:1,result:{isError:false,structuredContent:request,content:[]}});
 };
 assert.deepEqual(await createSpaceMachineReader(credentials,{send})(request.queueId),request);
});

test('fails closed on authorization, wrong RPC lineage and bounded/invalid body',async()=>{
 for(const [make,pattern] of [
  [()=>new Response(null,{status:403}),/NOT_VERIFIED/],
  [()=>Response.json({jsonrpc:'2.0',id:9,result:{isError:false}}),/UNAVAILABLE/],
  [()=>Response.json({jsonrpc:'2.0',id:1,result:{isError:true}}),/NOT_VERIFIED/],
  [()=>new Response('x',{headers:{'Content-Type':'application/json','Content-Length':'1000000'}}),/UNAVAILABLE/],
  [()=>new Response('<html>'),/UNAVAILABLE/],
 ]){
  const read=createSpaceMachineReader(credentials,{send:async()=>make()});
  await assert.rejects(read('space.q1'),pattern);
 }
});
