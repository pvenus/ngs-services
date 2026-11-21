import assert from 'node:assert/strict';
import {createServer,request as httpRequest} from 'node:http';
import test from 'node:test';
import {postServiceHeartbeat,startServiceHeartbeat} from './service-heartbeat.mjs';

test('heartbeat uses the fixed loopback contract and requires a real HTTP 200 acknowledgement',async t=>{
 const replies=[[200,'{"ok":true}'],[503,'{"ok":true}'],[200,'{"ok":false}'],[200,'{"ok":true,"other":1}'],[200,' '.repeat(2048)]];
 const requests=[];
 const server=createServer(async(req,res)=>{
  let body='';for await(const chunk of req)body+=chunk;
  requests.push([req.method,req.url,req.headers['content-type'],JSON.parse(body)]);
  assert.equal(req.headers.authorization,undefined);
  assert.equal(req.headers.cookie,undefined);
  const [status,reply]=replies.shift();
  res.writeHead(status,{'Content-Type':'application/json'}).end(reply);
 });
 await new Promise(ok=>server.listen(0,'127.0.0.1',ok));
 t.after(()=>new Promise(ok=>server.close(ok)));
 const request=(url,options,cb)=>{
  assert.equal(url,'http://127.0.0.1:8090/api/service-heartbeat');
  assert.equal(options.agent,false);
  assert.equal(options.signal.aborted,false);
  return httpRequest('http://127.0.0.1:'+server.address().port+'/api/service-heartbeat',options,cb);
 };
 const signal=new AbortController().signal;
 for(const expected of [true,false,false,false,false]){
  assert.equal(await postServiceHeartbeat(signal,{request}),expected);
 }
 assert.deepEqual(requests,Array.from({length:5},()=>[
  'POST','/api/service-heartbeat','application/json',{serverName:'rs'},
 ]));
});

test('start sends immediately, repeats every 60s, survives failures; stop aborts and clears',async()=>{
 let tick,unref=0,cancelled=0,attempts=0,finish,shutdown;
 const handle={unref(){unref++;}};
 const send=signal=>{
  attempts++;
  if(attempts===1)throw Error('unreachable');
  if(attempts===2)return new Promise(ok=>{finish=ok;});
  shutdown=signal;
  return new Promise(ok=>signal.addEventListener('abort',ok,{once:true}));
 };
 const stop=startServiceHeartbeat({send,every(fn,ms){
  assert.equal(ms,60000);tick=fn;return handle;
 },cancel(id){assert.equal(id,handle);cancelled++;}});
 assert.equal(attempts,1);
 assert.equal(unref,1);
 const inFlight=tick();
 await tick();
 assert.equal(attempts,2);
 finish(false);
 await inFlight;
 const last=tick();
 assert.equal(attempts,3);
 stop();stop();
 await last;
 await tick();
 assert.equal(cancelled,1);
 assert.equal(shutdown.aborted,true);
 assert.equal(attempts,3);
});
