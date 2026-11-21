import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import {runInNewContext} from 'node:vm';

const app=readFileSync(new URL('./app.js',import.meta.url),'utf8');
function node(){
 return {children:[],textContent:'',dataset:{},append(...items){this.children.push(...items)},
  replaceChildren(...items){this.children=items}};
}

test('service tab shows observed NGS, services and workers in deployment order with shared colors',async()=>{
 const ids=Object.fromEntries(['service-rows','service-empty','service-checked-at','refresh'].map(k=>[k,node()]));
 const stamp='2026-11-20T04:02:00Z',sha='a'.repeat(40);
 const state={state:'connected',missed:0,lastReceivedAt:stamp};
 let services=[{...state,serverName:'document-worker'},{...state,serverName:'unknown-service'},
  {...state,serverName:'ngs-local',commitSha:sha},{...state,serverName:'ngs-space',commitSha:null}];
 runInNewContext(app,{document:{getElementById:k=>ids[k],createElement:node,addEventListener(){}},AbortSignal,
  fetch:async path=>{assert.equal(path,'/api/service-status');return {ok:true,json:async()=>({services,checkedAt:stamp})}},
  setInterval:()=>0});
 await new Promise(resolve=>setImmediate(resolve));
 const groups=ids['service-rows'].children;
 assert.deepEqual(groups.map(g=>[g.className,g.dataset.group,g.children[0].textContent]),
  [['deploy-group','ngs','NGS'],['deploy-group','services','서비스'],['deploy-group','workers','워커']]);
 const cards=groups.map(g=>g.children[1].children.map(r=>r.children.map(c=>c.textContent)));
 assert.deepEqual(cards.map(group=>group.map(row=>row[0])),
  [['NGS Local','NGS Space'],['unknown-service'],['document-worker']]);
 assert.match(cards[0][0][2],/^마지막 연락 /);
 assert.equal(cards[0][0][3],'커밋 '+sha);
 assert.equal(cards[0][1][3],'커밋 확인 안 됨');
 services=[{...state,serverName:'rs'}];await ids.refresh.onclick();
 assert.deepEqual(ids['service-rows'].children.map(g=>g.dataset.group),['services']);
 services=[];await ids.refresh.onclick();
 assert.deepEqual(ids['service-rows'].children,[]);
 assert.match(ids['service-empty'].textContent,/헬스체크를 받은 서버가 없습니다/);
});
