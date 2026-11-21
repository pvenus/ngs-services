import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import {runInNewContext} from 'node:vm';

const code=readFileSync(new URL('./deployments.js',import.meta.url),'utf8');
const csrf='f'.repeat(64),a='a'.repeat(40),b='b'.repeat(40);
const date='2026-11-21T18:01:02Z',newDate='2026-11-21T18:08:15Z';
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const layout={ngs:['ngs-local','ngs-space'],services:['request-service','rs-mcp','workflow-planner','backend-stack'],
 workers:['worker-document','worker-file','worker-image','worker-animation','worker-postprocess','worker-audio']};
function data({all=true,running=false,compare=null}={}){
 const groups=Object.entries(layout).map(([id,targets])=>({id,label:id,targets:[...targets],canDeploy:true}));
 return {csrf,observed:{local:{sha:a,deployedAt:date},space:{sha:b,deployedAt:newDate}},cycle:{groups,
  comparison:compare,canDeployAll:all,targets:groups.flatMap(({id,targets})=>targets.map(target=>({
   id:target,label:target,group:id,state:target==='ngs-space'&&running?'running':target==='worker-image'?'blocked':
    target==='worker-file'?'not_configured':'ready',
   candidateSha:target==='worker-file'?'e'.repeat(40):a,
   observed:target==='worker-file'?{sha:b,deployedAt:'2026-02-30T01:05:01Z'}:
    target==='worker-document'?{sha:a,deployedAt:date}:null,
   canDeploy:!['worker-file','worker-image'].includes(target),
  })))}};
}
function fixtures(fetcher,extras={}){
 const ids=new Map(),hits=[],scheduled=[];
 const document={getElementById(id){if(!ids.has(id))ids.set(id,node());return ids.get(id);},createElement:node};
 function node(tag){
  const events=new Map();
  return {tag,children:[],attrs:{},dataset:{},className:'',textContent:'',value:'',hidden:false,disabled:false,
   append(...xs){this.children.push(...xs);},replaceChildren(...xs){this.children=xs;},
   setAttribute(k,v){this.attrs[k]=v;},addEventListener(name,f){events.set(name,f);},
   fire(name,event){events.get(name)?.(event);},focus(){document.activeElement=this;},select(){this.selected=true;}};
 }
 runInNewContext(code,{document,Intl,fetch(...args){hits.push(args);return fetcher(...args);},
  clearTimeout(){},setTimeout(f,delay){scheduled.push({f,delay});return scheduled.length;},...extras});
 const of=id=>document.getElementById(id);
 const find=(root,condition)=>condition(root)?root:root.children.map(x=>find(x,condition)).find(Boolean);
 const row=target=>find(of('deploy-groups'),n=>n.dataset.deployTarget===target);
 return {of,row,find,hits,scheduled};
}
test('grouped page shows 12 known deploy targets and verified evidence only; OPS cannot become a target',async()=>{
 const model=data(),fake={id:'ops',label:'OPS',group:'services',state:'ready',canDeploy:true};
 model.cycle.groups.find(x=>x.id==='services').targets.push(fake.id);model.cycle.targets.push(fake);
 const app=fixtures(async()=>Response.json(model));
 await flush();
 assert.deepEqual(app.of('deploy-groups').children.map(x=>x.dataset.group),['ngs','services','workers']);
 for(const id of [...layout.ngs,...layout.services,...layout.workers])assert.ok(app.row(id),id);
 assert.equal(app.row('ops'),undefined);
 const findText=(target,text)=>app.find(app.row(target),n=>n.textContent===text);
 assert.ok(findText('worker-image','차단됨'));
 assert.ok(findText('worker-file','배포 경로 미설정'));
 assert.ok(findText('worker-file','확인된 배포 없음'));
 assert.equal(app.find(app.row('worker-file'),n=>n.tag==='time'),undefined);
 assert.equal(app.find(app.row('worker-document'),n=>n.tag==='code').title,a);
 assert.equal(app.find(app.row('worker-document'),n=>n.tag==='time').dateTime,date);
 assert.match(app.find(app.of('deploy-groups'),n=>n.className.includes('deploy-comparison')).textContent,/버전 불일치/);
 assert.equal(app.of('deploy-all').disabled,false);
});
test('manual row and all issue only wake for selected scope; disabled state cannot send',async()=>{
 const app=fixtures(async(_url,{method})=>method==='POST'?Response.json({}, {status:202}):Response.json(data()));
 await flush();
 const disabled=app.find(app.row('worker-file'),n=>n.tag==='button');
 assert.equal(disabled.disabled,true);disabled.fire('click');assert.equal(app.hits.length,1);
 app.find(app.row('worker-document'),n=>n.tag==='button').fire('click');await flush();
 app.of('deploy-all').fire('click');await flush();
 const actions=app.hits.filter(([,o])=>o.method==='POST');
 assert.equal(actions.length,2);
 for(const [expected,[url,opts]] of [['worker-document',actions[0]],['all',actions[1]]]){
  assert.equal(url,'/api/deployments');assert.equal(opts.headers['X-OPS-Deploy-CSRF'],csrf);
  assert.deepEqual(JSON.parse(opts.body),{action:'wake',target:expected});
 }
});
test('running status polls but never claims unmatched last-known deployment during active NGS rollout',async()=>{
 const app=fixtures(async()=>Response.json(data({all:false,running:true})));
 await flush();
 assert.equal(app.of('deploy-all').disabled,true);
 assert.equal(app.find(app.row('ngs-space'),n=>n.tag==='button').disabled,true);
 assert.match(app.of('deploy-status').textContent,/자동으로 확인/);
 assert.equal(app.scheduled.length,1);
 assert.doesNotMatch(app.find(app.of('deploy-groups'),n=>n.className.includes('deploy-comparison')).textContent,/불일치/);
});
test('authorization boundary and missing configuration never expose executable buttons',async()=>{
 for(const [response,open,message] of [[401,true,/소유자 확인/],[503,false,/구성되지/]]){
  const app=fixtures(async()=>new Response(null,{status:response}));
  await flush();
  assert.equal(app.of('deploy-details').hidden,true);
  assert.equal(app.of('deploy-gate').hidden,!open);
  assert.match(app.of('deploy-status').textContent,message);
  app.of('deploy-all').fire('click');assert.equal(app.hits.length,1);
 }
});

test('Space uses a fixed publication destination and unlinked Space cannot be deployed',async()=>{
 const model=data(),space=model.cycle.targets.find(x=>x.id==='ngs-space');
 space.state='not_configured';space.canDeploy=true;space.destination='untrusted.example';
 const app=fixtures(async()=>Response.json(model));await flush();
 const row=app.row('ngs-space');
 assert.ok(app.find(row,n=>n.textContent==='게시 대상: vinus-npcgen.park-venus0518.chatgpt.site'));
 assert.ok(app.find(row,n=>n.textContent==='자동 게시 미연결'));
 assert.equal(app.find(row,n=>n.textContent.includes('untrusted.example')),undefined);
 assert.equal(app.find(row,n=>n.tag==='button').disabled,true);
 assert.equal(app.of('deploy-all').disabled,true);
 assert.match(app.find(app.of('deploy-groups'),n=>n.className.includes('deploy-comparison')).textContent,/버전 불일치/);
 app.find(row,n=>n.tag==='button').fire('click');app.of('deploy-all').fire('click');
 assert.equal(app.hits.length,1);
 space.state='ready';
 app.of('deploy-refresh').fire('click');await flush();
 assert.equal(app.find(app.row('ngs-space'),n=>n.textContent==='자동 게시 미연결'),undefined);
 assert.ok(app.find(app.row('ngs-space'),n=>n.textContent==='배포 준비'));
 assert.equal(app.find(app.row('ngs-space'),n=>n.tag==='button').disabled,false);
});

test('verified Space thread handoff is manual only and keeps all deployment disabled',async()=>{
 const model=data(),space=model.cycle.targets.find(x=>x.id==='ngs-space');
 const url='https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef';
 space.state='not_configured';space.canDeploy=false;
 space.publication={provider:'chatgpt-sites',hostname:'vinus-npcgen.park-venus0518.chatgpt.site',handoff:{url,sha:a}};
 const opens=[],copies=[];
 const app=fixtures(async()=>Response.json(model),{window:{open(...args){opens.push(args)}},
  navigator:{clipboard:{async writeText(text){copies.push(text)}}}});
 await flush();
 const button=app.find(app.row('ngs-space'),n=>n.tag==='button');
 assert.equal(button.disabled,false);
 assert.equal(button.textContent,'배포 스레드에 전달');
 assert.equal(app.of('deploy-all').disabled,true);
 button.fire('click');await flush();
 assert.deepEqual(opens,[[url,'_blank','noopener,noreferrer']]);
 assert.deepEqual(copies,['NGS Space를 pvenus/ngs-platform 커밋 '+a+'로 게시해 주세요. 게시 대상: vinus-npcgen.park-venus0518.chatgpt.site.']);
 assert.match(app.of('deploy-status').textContent,/직접 전송/);
 assert.equal(app.hits.length,1);
 space.publication.handoff.url='https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef?redirect=https://example.com';
 app.of('deploy-refresh').fire('click');await flush();
 assert.equal(app.find(app.row('ngs-space'),n=>n.tag==='button').disabled,true);
});

test('thread identity alone opens native Codex and copies only the fixed request',async()=>{
 const model=data(),space=model.cycle.targets.find(x=>x.id==='ngs-space');
 const url='codex://threads/01234567-89ab-cdef-0123-456789abcdef';
 space.state='not_configured';space.canDeploy=false;
 space.publication={provider:'chatgpt-sites',hostname:'vinus-npcgen.park-venus0518.chatgpt.site',handoff:{url}};
 const opens=[],copies=[];
 const app=fixtures(async()=>Response.json(model),{window:{open(...args){opens.push(args)}},
  navigator:{clipboard:{async writeText(text){copies.push(text)}}}});
 await flush();
 app.find(app.row('ngs-space'),n=>n.tag==='button').fire('click');await flush();
 assert.deepEqual(opens,[[url,'_blank','noopener,noreferrer']]);
 assert.deepEqual(copies,['비나리 스페이스 최신 검증 버전을 배포해줘.']);
 assert.equal(app.hits.length,1);
 for(const handoff of [{url:url+'\n'},{url:url+'/extra'},{url,sha:'abc'}]){
  space.publication.handoff=handoff;app.of('deploy-refresh').fire('click');await flush();
  assert.equal(app.find(app.row('ngs-space'),n=>n.tag==='button').disabled,true);
 }
});

test('verified Dot thread ID without a provider URL copies without pretending to send',async()=>{
 const model=data(),space=model.cycle.targets.find(x=>x.id==='ngs-space');
 space.state='not_configured';space.canDeploy=false;
 space.publication={provider:'chatgpt-sites',hostname:'vinus-npcgen.park-venus0518.chatgpt.site',
  handoff:{threadId:'01234567-89ab-cdef-0123-456789abcdef',title:'비나리 스페이스 배포'},
  live:{commitSha:null,state:'connected'}};
 const copies=[];
 const app=fixtures(async()=>Response.json(model),{window:{open(){throw Error('no verified URL')}},
  navigator:{clipboard:{async writeText(text){copies.push(text)}}}});
 await flush();
 const row=app.row('ngs-space'),button=app.find(row,n=>n.tag==='button');
 assert.equal(button.textContent,'배포 요청 복사');
 assert.ok(app.find(row,n=>n.textContent==='NGS-MCP: 응답 확인 · 현재 커밋 미확인'));
 button.fire('click');await flush();
 assert.deepEqual(copies,['비나리 스페이스 최신 검증 버전을 배포해줘.']);
 assert.match(app.of('deploy-status').textContent,/비나리 스페이스 배포/);
 assert.equal(app.hits.length,1);
});
