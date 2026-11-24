import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {chmodSync,mkdtempSync,readFileSync,realpathSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL,fileURLToPath} from 'node:url';
import {test} from 'node:test';
import {PlannerStore} from '../workflow-planner/planner.mjs';
import {createRequestService} from '../request-service/request-service.mjs';
import {createRSClient,makeLoopbackTransport} from '../workflow-planner/rs.mjs';
import {pollOnce} from '../workflow-planner/planner.mjs';

const workerPath=process.env.PROP_IMAGE_WORKER_MODULE,webPath=process.env.PROP_NGS_WEB_MODULE;
const base=new URL('..',import.meta.url);
const submit=JSON.parse(readFileSync(new URL('docs/prop-design/create-request.json',base)));
const markdown=readFileSync(new URL('docs/prop-design/content.md',base),'utf8');

test('existing category request creates local Filex Markdown and real NGS read & prop image worker consume it',
 {skip:!workerPath||!webPath},async t=>{
 const root=mkdtempSync(join(realpathSync(tmpdir()),'prop-design-join-'));
 const paths=[join(root,'official'),join(root,'planner.sqlite')];
 const rs=createRequestService({directory:paths[0]});await new Promise(resolve=>rs.listen(0,'127.0.0.1',resolve));
 const planner=new PlannerStore(paths[1]);chmodSync(paths[1],0o600);
 let ngs;
 t.after(async()=>{if(ngs)await new Promise(resolve=>ngs.close(resolve));
  planner.close();await new Promise(resolve=>rs.close(resolve));rmSync(root,{recursive:true,force:true});});
 const bridge=makeLoopbackTransport(rs.address().port),client=createRSClient({transport:bridge});
 const response=await bridge({jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'request_submit',arguments:submit}});
 assert.equal(response.result.isError,false,JSON.stringify(response));
 const {officialRequestId}=response.result.structuredContent;
 assert.equal((await pollOnce(planner,client)).acknowledged,1);
 const row=planner.db.prepare('SELECT t.task_id,t.task_key,r.payload_sha256 FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)').get();
 assert.equal(row.task_key,'category_generate');
 const claimStamp=Date.now()+1;
 assert.equal(planner.db.prepare("UPDATE workflow_tasks SET status='running',updated_at=? WHERE task_id=? AND status='pending'").run(claimStamp,row.task_id).changes,1);
 const claim={officialRequestId,taskId:row.task_id,projectId:'binari',categoryId:submit.payload.categoryId,payloadSha256:row.payload_sha256,claimStamp};
 const run=spawnSync('python3',['-B',fileURLToPath(new URL('tests/prop-design-local-fixture.py',base)),root],{
  encoding:'utf8',input:JSON.stringify(claim),env:{PATH:process.env.PATH,PYTHONPATH:fileURLToPath(new URL('data-service/',base))},
 });
 assert.equal(run.status,0,run.stderr);
 const context=JSON.parse(run.stdout);
 assert.deepEqual(context.sources.map(p=>p.role),['master-design','prop-design']);
 assert.equal(context.sources[1].markdown,markdown);
 assert.doesNotMatch(JSON.stringify(context),/do_not_forward_narrative/);
 const {createLocalProjectPageServer}=await import(pathToFileURL(webPath));
 ngs=createLocalProjectPageServer({root:join(root,'replica')});
 await new Promise(resolve=>ngs.listen(0,'127.0.0.1',resolve));
 const origin='http://127.0.0.1:'+ngs.address().port;
 const concept='/api/project-workspace?projectId=binari&tab=concept';
 assert.equal((await fetch(origin+concept)).status,401);
 const session=await fetch(origin+'/__npcgen_session'),cookie=session.headers.get('set-cookie').split(';')[0];
 const get=async path=>{
  const r=await fetch(origin+path,{headers:{cookie}});assert.equal(r.status,200,await r.clone().text());return r.json();
 };
 const index=await get(concept);
 assert.ok(index.items.some(r=>r.id===claim.categoryId&&r.label==='프랍 디자인 설계'&&r.groupId==='group.art-design'));
 const view=await get(concept+'&item='+claim.categoryId);
 const body=await get(concept+'&item='+claim.categoryId+'&file='+encodeURIComponent(view.files[0].path));
 assert.match(body.markdown,/상태 사이의 일관성/);
 assert.equal(body.markdown,markdown);
 const worker=await import(pathToFileURL(workerPath)),input={schemaVersion:worker.propImageRequestSchema,projectId:'binari',
  propId:'candle-sample',state:'unlit',lane:'detail',intent:'실제 요청 기준의 밀랍 초',designContextSha256:context.contextSha256};
 let calls=0;
 const renderProp=async actual=>{
  calls++;assert.equal(actual.intent,input.intent);assert.deepEqual(actual.sources,context.sources);
  const native=join(fileURLToPath(new URL('.',pathToFileURL(workerPath))),'portrait-native.py');
  const simulate=`
import hashlib,json,runpy,sys
m=runpy.run_path(sys.argv[1]);main=m['main']; calls=[]
def digest(v):
 return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
image=bytes.fromhex('89504e470d0a1a0a')+b'fixture'
class Pairs:
 @staticmethod
 def descriptor(prompt,ref):
  assert ref is None
  return {'instruction':prompt,'outputContract':{'alpha':True}}
 @staticmethod
 def reference(*_):raise AssertionError('no NPC/reference lookup')
class FakeProvider:
 def generate(self,claim):
  assert not calls and claim['index']==0 and 'referenceManifest' not in claim
  calls.append(claim['providerRequest']['instruction'])
  return image,{'actualToolOutput':True,'sha256':hashlib.sha256(image).hexdigest(),**claim}
 def execution_evidence(self):return {'outcome':'joined'}
def alpha(raw,proof,profile):assert raw==image and proof=={'alpha':True} and profile=='portrait'
main.__globals__['bind']=lambda *_:(lambda *_:FakeProvider(),Pairs(),{},digest,alpha)
sys.argv=['portrait-native.py','render-prop','fixture','fixture']
main()
assert len(calls)==1
print(calls[0],file=sys.stderr)
`;
  const proc=spawnSync('python3',['-B','-c',simulate,native],{input:JSON.stringify(actual)});
  assert.equal(proc.status,0,proc.stderr);
  assert.equal(proc.stdout.toString('hex'),'89504e470d0a1a0a'+Buffer.from('fixture').toString('hex'));
  const prompt=proc.stderr.toString('utf8');
  const parts=['[1 Master design]','[2 Prop design]','[3 Exact request]'];
  assert.ok(parts.every(x=>prompt.includes(x))&&parts.every((x,i)=>i===0||prompt.indexOf(parts[i-1])<prompt.indexOf(x)));
  assert.match(prompt,/master_fixture_1/);assert.match(prompt,/master_fixture_2/);
  assert.match(prompt,/상태 사이의 일관성/);assert.match(prompt,/실제 요청 기준의 밀랍 초/);
  assert.doesNotMatch(prompt,/do_not_forward_/);
  return proc.stdout;
 };
 const runWorker=(ctx,req=input)=>worker.runPropImage({request:req,pds:{readPropDesign:async id=>{
  assert.equal(id,'binari');return ctx;
 }},renderProp});
 const success=await runWorker(context);
 assert.equal(success.contextSha256,context.contextSha256);assert.equal(calls,1);
 for(const bad of [{...context,sources:[context.sources[1],context.sources[0]]},
  {...context,sources:[...context.sources,{role:'unrelated'}]},
  {...context,sources:[context.sources[0],{...context.sources[1],markdown:'wrong'}]}]){
  await assert.rejects(runWorker(bad),/PROP_IMAGE_DESIGN_INVALID/);
 }
 await assert.rejects(runWorker(context,{...input,designContextSha256:'0'.repeat(64)}),/PROP_IMAGE_DESIGN_CHANGED/);
 assert.equal(calls,1);
});
