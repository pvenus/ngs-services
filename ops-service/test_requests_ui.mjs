import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import {runInNewContext} from 'node:vm';

const code=readFileSync(new URL('./app.js',import.meta.url),'utf8');
test('request list asks for resubmission only for the exact unsupported failed reason',async()=>{
 const fixture=[
  {status:'failed',failureReason:'REQUEST_UNSUPPORTED_RECREATE'},
  {status:'failed',failureReason:'private text'},
  {status:'registered',failureReason:'REQUEST_UNSUPPORTED_RECREATE'},
 ];
 const elements=new Map();
 const node=()=>({children:[],textContent:'',dataset:{},
  append(...children){this.children.push(...children)},replaceChildren(...children){this.children=children},
  addEventListener(){},setAttribute(){}});
 const document={getElementById(id){if(!elements.has(id))elements.set(id,node());return elements.get(id)},
  createElement:node,addEventListener(){}};
 const requests=fixture.map((row,i)=>({...row,officialRequestId:'request.official.'+String(i).repeat(64),
  sourceId:'local',projectId:'binari',requestType:'npc.workflow',registeredAt:1}));
 runInNewContext(code,{document,AbortController,URLSearchParams,location:{search:''},
  fetch:async()=>Response.json({requests,nextCursor:null})});
 await new Promise(resolve=>setImmediate(resolve));
 const states=elements.get('rows').children.map(row=>row.children[1]);
 assert.equal(states.length,3);
 assert.equal(states[0].textContent,'실패');
 assert.equal(states[0].children[0].textContent,'이 버전에서 지원하지 않는 요청입니다. 새로 요청해 주세요.');
 assert.equal(states[1].children.length,0);
 assert.equal(states[2].children.length,0);
});

test('2D split progress distinguishes generation, encode and the exact eight-direction publish barrier',()=>{
 const node=()=>({children:[],textContent:'',dataset:{},append(){},replaceChildren(){},addEventListener(){},setAttribute(){}});
 const document={getElementById:node,createElement:node,addEventListener(){}};
 const api=runInNewContext(code+'\n;({stateFor,taskName});',{document,AbortController,URLSearchParams,location:{search:''},
  fetch:async()=>Response.json({requests:[],nextCursor:null})});
 const final={key:'motion_2d_finalize',type:'motion-preset.convert-2d.finalize',workerType:'postprocess',
  status:'pending',dependsOn:null};
 const parents=new Map(['n','ne','e','se','s','sw','w','nw'].map(d=>['motion_2d_encode_'+d,
  {type:'motion-preset.convert-2d.encode',workerType:'postprocess',status:d==='n'?'running':'completed'}]));
 assert.equal(api.taskName('motion_2d_frames_n'),'북 PNG 프레임 생성');
 assert.equal(api.taskName('motion_2d_encode_n'),'북 MP4 인코딩');
 assert.equal(api.stateFor(final,parents,'planned'),'pending');
 parents.get('motion_2d_encode_n').status='failed';
 assert.equal(api.stateFor(final,parents,'planned'),'blocked');
 parents.get('motion_2d_encode_n').status='completed';
 assert.equal(api.stateFor(final,parents,'planned'),'ready');
 parents.delete('motion_2d_encode_ne');
 assert.equal(api.stateFor(final,parents,'planned'),'pending');
});
