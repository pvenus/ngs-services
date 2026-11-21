import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import {runInNewContext} from 'node:vm';

const app=readFileSync(new URL('./app.js',import.meta.url),'utf8');
const id='request.official.'+'a'.repeat(64);
const alarm={officialRequestId:id,key:'portrait_n',workerType:'image',retryCount:3,updatedAt:1784700000000,hasRequest:true};
const flush=()=>new Promise(resolve=>setImmediate(resolve));

function node(){
 return {children:[],textContent:'',hidden:false,dataset:{},append(...xs){this.children.push(...xs)},
  replaceChildren(...xs){this.children=xs},addEventListener(){},showModal(){this.open=true}};
}

test('alarm tab renders one exhausted row, links exact request and never starts a timer',async()=>{
 const ids=Object.fromEntries(['alarm-status','alarm-list','alarm-rows','refresh'].map(k=>[k,node()]));
 const hits=[];let ok=true;
 runInNewContext(app,{document:{getElementById:k=>ids[k],createElement:node},AbortSignal,URLSearchParams,
  fetch:async path=>{hits.push(path);return {ok,json:async()=>({alarms:[alarm],hasMore:false})}},
  setInterval(){assert.fail('alarms must not poll')}});
 await flush();
 assert.deepEqual(hits,['/api/alarms']);
 assert.equal(ids['alarm-rows'].children.length,1);
 const [name,worker,retries,,request]=ids['alarm-rows'].children[0].children;
 assert.equal(name.textContent,'북 포트레이트');
 assert.equal(worker.textContent,'이미지');
 assert.equal(retries.textContent,'3회');
 assert.equal(request.children[0].href,'/requests?requestId='+id);
 assert.equal(ids['alarm-list'].hidden,false);
 ok=false; await ids.refresh.onclick();
 assert.equal(ids['alarm-list'].hidden,true);
 assert.match(ids['alarm-status'].textContent,/불러오지 못했습니다/);
});

test('exact alarm link opens existing Request progress dialog',()=>{
 const ids=Object.fromEntries(['rows','notice','page','next','prev','refresh','status-filter','progress-dialog',
  'progress-close','progress-target','progress-rows','progress-list','progress-updated','progress-summary'].map(k=>[k,node()]));
 const hits=[];
 runInNewContext(app,{document:{getElementById:k=>ids[k],createElement:node,addEventListener(){}},
  location:{search:'?requestId='+id},AbortSignal,AbortController,URLSearchParams,
  fetch:path=>{hits.push(path);return new Promise(()=>{})},setInterval:()=>0});
 assert.equal(ids['progress-dialog'].open,true);
 assert.equal(ids['progress-target'].title,id);
 assert.deepEqual(hits,['/api/official-requests?limit=25','/api/request-progress?requestId='+id]);
});
