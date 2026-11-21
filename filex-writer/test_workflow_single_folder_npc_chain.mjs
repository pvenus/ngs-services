// Isolated cross-repository fixture. Arguments: RS, Planner, File, Document release dirs.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {chmodSync,existsSync,mkdtempSync,readFileSync,realpathSync,rmSync,statSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createInterface} from 'node:readline';
import {fileURLToPath} from 'node:url';

const [rsDir,plannerDir,fileDir,docDir]=process.argv.slice(2);
const load=(root,name)=>import(join(realpathSync(root),name));
const {createRequestService,configuration}=await load(rsDir,'request-service.mjs');
const {PlannerStore,pollOnce}=await load(plannerDir,'planner.mjs');
const {createRSClient,makeLoopbackTransport}=await load(plannerDir,'rs.mjs');
const {pollOnce:fileTick}=await load(fileDir,'runner.mjs');
const file=await load(fileDir,'local-ports.mjs');
const {documentTick}=await load(docDir,'runner.mjs');
const doc=await load(docDir,'local-ports.mjs');

const temp=realpathSync(mkdtempSync(join(tmpdir(),'npc-single-target-full-chain-')));
const dbPath=join(temp,'workflow.sqlite'),store=new PlannerStore(dbPath);chmodSync(dbPath,0o600);
const rs=createRequestService(configuration({REQUEST_OFFICIAL_INBOX_DIR:join(temp,'inbox')}));
await new Promise(resolve=>rs.listen(0,'127.0.0.1',resolve));
const rsPort=rs.address().port,client=createRSClient({transport:makeLoopbackTransport(rsPort)});
const proc=spawn(process.env.PYTHON??'/opt/homebrew/bin/python3.12',['-B','-u',fileURLToPath(new URL('./test_workflow_single_folder_npc_http.py',import.meta.url)),dbPath],{stdio:['pipe','pipe','inherit']});
const done=once(proc,'close');
let fixture;
try{
 const lines=createInterface({input:proc.stdout})[Symbol.asyncIterator]();
 fixture=JSON.parse((await lines.next()).value);
 const {port,root,oldGeneration}=fixture;
 const payload={schemaVersion:'ngs.npc-one-stop.replace.v1',projectId:'binari',intent:'One isolated test NPC only',
  character:{npcId:'npc.test',name:'테스트 인물',slug:'fixture',role:'마을 주민',actId:'act01',chapterId:'chapter01'},
  sourceRefs:['fixture.story'],includeCombat:false};
 const response=await fetch('http://127.0.0.1:'+rsPort+'/mcp',{method:'POST',headers:{Accept:'application/json, text/event-stream','Content-Type':'application/json','MCP-Protocol-Version':'2025-06-18'},
  body:JSON.stringify({jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'request_submit',
   arguments:{sourceId:'local',requestType:'npc.workflow',idempotencyKey:'npc-fixture-single-target',payload}}})});
 const body=await response.json();assert.equal(body.result?.isError,false,JSON.stringify(body));
 const official=body.result.structuredContent.officialRequestId;
 const staged=await pollOnce(store,client);assert.equal(staged.acknowledged,1,JSON.stringify(staged));
 const row=key=>store.db.prepare('SELECT * FROM workflow_tasks WHERE official_request_id=? AND task_key=?').get(official,key);
 const pds=file.localPorts({singleTarget:true,transport:o=>file.requestLocalJson({...o,port})});
 const dps=doc.localPorts({transport:o=>doc.requestLocalJson({...o,port})});
 const preset=()=>documentTick({dbPath,pds:dps,operation:'npc.preset'});
 const canonical=join(root,'npc.test'),sibling=join(root,'npc.other','metadata.json');
 const inode=statSync(canonical).ino,siblingBytes=readFileSync(sibling),siblingInode=statSync(sibling).ino;
 assert.equal((await preset()).state,'idle');
 const deletion=await fileTick({dbPath,pds});
 assert.equal(deletion.state,'completed',JSON.stringify(deletion));assert.equal(row('delete').result_ref,row('delete').task_id);
 assert.equal(existsSync(canonical),false);
 const receiptFile=join(root,'.requests',row('delete').task_id+'.json');
 const {sha}=await load(plannerDir,'envelope.mjs');
 const receipt=JSON.parse(readFileSync(existsSync(receiptFile)?receiptFile:join(root,'.requests',sha(row('delete').task_id)+'.json'),'utf8'));
 const saved=join(root,'.storage-quarantine',receipt.storageRecoveryId,'target');
 assert.equal(statSync(saved).ino,inode);assert(existsSync(join(saved,'nested','empty')));
 assert.equal(receipt.generationId,oldGeneration);
 const publication=await preset();
 assert.equal(publication.state,'completed',JSON.stringify(publication));assert.equal(row('preset').status,'completed');
 const state=JSON.parse(readFileSync(join(root,'.generations','npc.test.json'),'utf8'));
 assert.equal(state.previousGenerationId,oldGeneration);assert.notEqual(state.generationId,oldGeneration);
 assert.equal(statSync(saved).ino,inode);assert.deepEqual(readFileSync(sibling),siblingBytes);assert.equal(statSync(sibling).ino,siblingInode);
 assert.equal(row('planning_overview').status,'pending');
 console.log('PASS isolated real RS→Planner→File Worker→PDS→Filex folder rename→Document preset via exact predecessor receipt, neighbor byte/inode unchanged');
}finally{
 if(fixture)proc.stdin.end('close\n');else proc.stdin.end();
 const [code]=await done;assert.equal(code,0);
 await new Promise(resolve=>rs.close(resolve));store.close();rmSync(temp,{recursive:true,force:true});
}
