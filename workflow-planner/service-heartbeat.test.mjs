import assert from 'node:assert/strict';
import test from 'node:test';
import {startPlannerHeartbeat} from './service-heartbeat.mjs';

for(const revision of ['a'.repeat(40),undefined,'legacy7']){
 test('fires immediately with canonical revision only and aborts on stop: '+revision,async()=>{
  const requests=[];
  let timeFn,canceled;
  const stop=startPlannerHeartbeat({revision,send:async (url,options)=>{
   requests.push({url,...options});
   return {};
  },every:(fn,ms)=>{assert.equal(ms,60_000);timeFn=fn;return 8;},cancel:n=>{canceled=n;}});
  assert.equal(requests.length,1);
  assert.equal(requests[0].url,'http://127.0.0.1:8090/api/service-heartbeat');
  assert.deepEqual(JSON.parse(requests[0].body),{serverName:'workflow-planner',
   ...(revision?.length===40?{commitSha:revision}:{})});
  await new Promise(setImmediate);
  await timeFn();
  assert.equal(requests.length,2);
  stop();
  assert.equal(canceled,8);
  assert.ok(requests.every(item=>item.signal.aborted));
  await timeFn();
  assert.equal(requests.length,2);
 });
}

test('overlapping or failed reports do not block Planner',async()=>{
 let timer,finish,count=0;
 const stop=startPlannerHeartbeat({send:()=>{count++;return new Promise((_,reject)=>{finish=reject;});},
  every:fn=>{timer=fn;return 1;},cancel:()=>{}});
 try{
  await timer();
  assert.equal(count,1);
  finish(Error('OPS absent'));
  await Promise.resolve();
  void timer();
  assert.equal(count,2);
  finish(Error('OPS absent'));
 }finally{stop();}
});
