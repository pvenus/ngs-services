// Real isolated RS → Planner → Audio Worker → HTTP PDS/Filex; injected PCM fixtures only.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {chmodSync,mkdtempSync,readFileSync,readdirSync,realpathSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createInterface} from 'node:readline';
import {fileURLToPath} from 'node:url';

const [contract,worker]=process.argv.slice(2).map(p=>realpathSync(p));
const load=(d,p)=>import(join(d,p));
const {createRequestService,configuration}=await load(contract,'rs/request-service.mjs');
const {PlannerStore,pollOnce}=await load(contract,'planner/planner.mjs');
const {createRSClient,makeLoopbackTransport}=await load(contract,'planner/rs.mjs');
const {runAudioFromPath,nextAudioFromPath}=await load(worker,'audio-worker.mjs');
const {audioPorts,requestLocalJson}=await load(worker,'audio-ports.mjs');
const tmp=realpathSync(mkdtempSync(join(tmpdir(),'workflow-audio-chain-')));
const dbPath=join(tmp,'workflow.sqlite'),store=new PlannerStore(dbPath);chmodSync(dbPath,0o600);
const rs=createRequestService(configuration({REQUEST_OFFICIAL_INBOX_DIR:join(tmp,'inbox')}));
await new Promise(resolve=>rs.listen(0,'127.0.0.1',resolve));
const rsPort=rs.address().port,client=createRSClient({transport:makeLoopbackTransport(rsPort)});
const py=spawn(process.env.PYTHON??'/opt/homebrew/bin/python3.12',['-B','-u',fileURLToPath(new URL('./test_workflow_audio_http.py',import.meta.url)),dbPath],{stdio:['pipe','pipe','inherit']});
const done=once(py,'close');
function wav(rate,seconds){
 const n=rate*2*seconds,b=Buffer.alloc(44+n);
 b.write('RIFF');b.writeUInt32LE(b.length-8,4);b.write('WAVE',8);b.write('fmt ',12);b.writeUInt32LE(16,16);
 b.writeUInt16LE(1,20);b.writeUInt16LE(1,22);b.writeUInt32LE(rate,24);b.writeUInt32LE(rate*2,28);
 b.writeUInt16LE(2,32);b.writeUInt16LE(16,34);b.write('data',36);b.writeUInt32LE(n,40);
 return b;
}
let booted=false;
try{
 const lines=createInterface({input:py.stdout})[Symbol.asyncIterator]();
 const {port,audio,conceptId}=JSON.parse((await lines.next()).value);booted=true;
 const ids={};
 for(const [variant,inputDuration,text] of [['legacy',10,''],['auto',null,'[Humming]\n음—']]){
  const kind='bgm';
  const payload={schemaVersion:'ngs.audio-generate.v1',projectId:'binari',kind,title:'실제 경로 시험',
   conceptId,brief:'isolated fixture only',text,durationSeconds:inputDuration};
  const response=await fetch('http://127.0.0.1:'+rsPort+'/mcp',{method:'POST',
   headers:{Accept:'application/json, text/event-stream','Content-Type':'application/json','MCP-Protocol-Version':'2025-06-18'},
   body:JSON.stringify({jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'request_submit',
    arguments:{sourceId:'local',requestType:'audio.generate',idempotencyKey:variant,payload}}})});
  const {result}=await response.json();assert.equal(result.isError,false,JSON.stringify(result));
  ids[variant]=result.structuredContent.officialRequestId;
 }
 let polled=await pollOnce(store,client);assert.equal(polled.acknowledged,2);
 const pds=audioPorts({transport:o=>requestLocalJson({...o,port})});
 assert(await pds.capability());
 const rates={bgm:48000},calls=[];
 const adapters=Object.fromEntries(Object.entries(rates).map(([kind,rate])=>[kind,async input=>{
  const variant=input.durationSeconds===null?'auto':'legacy';
  calls.push(variant);assert.equal(kind,input.kind);assert.match(input.prompt,/한글/);
  if(variant==='auto')assert.equal(input.text,'[Humming]\n음—');
  else assert(!Object.hasOwn(input,'text'));
  return {wav:wav(rate,variant==='auto'?73:10),bpm:80,bpmBasis:'model'};
 }]));
 for(let i=0;i<2;i++){
  const row=nextAudioFromPath(dbPath);
  const {officialRequestId,taskId}=row;
  const result=await runAudioFromPath({dbPath,taskId,officialRequestId,pds,adapters});
  assert.equal(result.state,'completed',JSON.stringify(result));
  assert.deepEqual(readdirSync(join(audio,officialRequestId)).sort(),['audio.wav','metadata.json']);
  const meta=JSON.parse(readFileSync(join(audio,officialRequestId,'metadata.json'),'utf8'));
  const variant=calls.at(-1),duration=variant==='auto'?73:10;
  assert.equal(meta.kind,'bgm');assert.equal(meta.officialRequestId,ids[variant]);assert.equal(meta.audio.sampleRate,rates[meta.kind]);
  assert.equal(meta.durationSeconds,variant==='auto'?null:10);
  assert.deepEqual([meta.bpm,meta.bpmBasis,meta.audio.durationSeconds],[80,'model',duration]);
  assert.deepEqual(readFileSync(join(audio,officialRequestId,'audio.wav')),wav(rates[meta.kind],duration));
 }
 assert.deepEqual(calls.slice().sort(),Object.keys(ids).sort());
 assert.equal(nextAudioFromPath(dbPath),null);
 polled=await pollOnce(store,client);assert.equal(polled.completed,2);
 assert.equal(readdirSync(audio).length,2);
 console.log('PASS actual isolated RS/Planner/audio-worker/PDS/Filex chain, BGM lane with injected PCM only');
}finally{
 if(booted)py.stdin.end('close\n');else py.stdin.end();
 const [code]=await done;
 await new Promise(resolve=>rs.close(resolve));store.close();rmSync(tmp,{recursive:true,force:true});
 assert.equal(code,0);
}
