'use strict';
const $=id=>document.getElementById(id);
let status='', stack=[null], cursor=null, request=0, controller=null;
const statusLabels=new Map([['registered','등록됨'],['planned','계획됨'],['completed','완료됨'],['failed','실패'],['cancelled','취소됨']]);
const cell=value=>{const el=document.createElement('td');el.textContent=value??'—';return el};
function requestIdCell(full,sourceId){
 const el=cell(''),short=document.createElement('code'),copy=document.createElement('button'),source=document.createElement('small');
 const key=full.replace(/^request\.official\./,'');
 short.textContent=key.length>18?key.slice(0,8)+'…'+key.slice(-6):key;
 short.title=full;
 copy.type='button';copy.className='copy-request-id';copy.textContent='복사';
 copy.setAttribute('aria-label','전체 요청 ID 복사');copy.title=full;
 copy.addEventListener('click',async()=>{
  try{
   await navigator.clipboard.writeText(full);
   copy.textContent='복사됨';
  }catch{
   short.textContent=full;
   const range=document.createRange();range.selectNodeContents(short);
   const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
   copy.textContent='선택됨';
  }
 });
 source.textContent=sourceId;el.append(short,copy,source);
 return el;
}
const date=value=>{const d=new Date(typeof value==='number'&&Math.abs(value)<1e12?value*1000:value);return Number.isNaN(d.valueOf())?String(value):d.toLocaleString('ko-KR')};
async function load(){
 const id=++request;controller?.abort();controller=new AbortController();
 $('notice').textContent='불러오는 중…';$('notice').className='';$('rows').replaceChildren();cursor=null;
 $('next').disabled=true;$('prev').disabled=stack.length<2;$('page').textContent=stack.length+'쪽';
 const q=new URLSearchParams({limit:'25'});if(status)q.set('status',status);if(stack.at(-1))q.set('cursor',stack.at(-1));
 try{
  const res=await fetch('/api/official-requests?'+q,{cache:'no-store',signal:controller.signal});
  if(!res.ok)throw Error('UNAVAILABLE');const data=await res.json();if(id!==request)return;
  for(const r of data.requests){
   const row=document.createElement('tr');row.dataset.status=r.status;const identifier=requestIdCell(r.officialRequestId,r.sourceId);
   const target=cell(r.targetName||r.targetNpcId);
   if(r.targetName&&r.targetNpcId){const id=document.createElement('small');id.textContent=r.targetNpcId;target.append(id);}
   const state=cell(statusLabels.get(r.status)??r.status);state.className='request-status';
   const detail=cell(''),button=document.createElement('button');
   button.type='button';button.className='progress-open';button.textContent='상세 보기';
   button.setAttribute('aria-label',(r.targetName||r.targetNpcId||'요청')+' 진행 상세');
   button.addEventListener('click',()=>openProgress(r));detail.append(button);
   row.append(identifier,state,target,cell(r.projectId+' / '+r.requestType),cell(date(r.registeredAt)),detail);$('rows').append(row);
  }
  $('notice').textContent=data.requests.length?data.requests.length+'건 표시':'표시할 공식 요청이 없습니다.';
  cursor=data.nextCursor;$('next').disabled=!cursor;
 }catch(error){if(id===request&&error.name!=='AbortError'){$('notice').className='error';$('notice').textContent='공식 요청을 불러오지 못했습니다. 잠시 뒤 새로고침해 주세요.'}}
}
let progressSelection=null,progressTimer=null,progressFetch=null;
const workerNames={file:'파일',document:'문서',image:'이미지',animation:'애니메이션',postprocess:'후처리',audio:'음원'};
const taskNames={audio_generate:'BGM 생성',delete:'기존 NPC 폴더 삭제',preset:'프리셋 생성',planning_representative:'대표 기획',
 portrait_representative:'대표 포트레이트',planning_idle:'아이들 기획',portrait_idle_reference:'아이들 기준 포트레이트'};
const directionNames={n:'북',ne:'북동',e:'동',se:'남동',s:'남',sw:'남서',w:'서',nw:'북서'};
function taskName(key){
 if(!key)return '—';
 if(Object.hasOwn(taskNames,key))return taskNames[key];
 const portrait=key.match(/^portrait_(n|ne|e|se|s|sw|w|nw)$/);
 if(portrait)return directionNames[portrait[1]]+' 포트레이트';
 const motion=key.match(/^(animation|frames)_(walk|run)_(n|ne|e|se|s|sw|w|nw)$/);
 if(motion)return directionNames[motion[3]]+' '+(motion[2]==='walk'?'걷기':'뛰기')+(motion[1]==='animation'?' 원본 PNG':' 프레임 후처리');
 return key;
}
function stateFor(task,keys,state){
 if(task.status!=='pending')return task.status;
 if(state!=='planned')return 'pending';
 const parent=keys.get(task.dependsOn);
 if(parent?.status==='failed')return 'blocked';
 return !task.dependsOn||parent?.status==='completed'?'ready':'pending';
}
const progressLabels={completed:'완료',running:'진행 중',ready:'인수 가능',pending:'대기',failed:'실패',blocked:'선행 실패'};
const progressRowId=key=>'progress-task-'+encodeURIComponent(key);
function dependencyCell(task,keys,selection){
 const parent=task.dependsOn,td=cell(taskName(parent));
 if(!keys.has(parent))return td;
 const link=document.createElement('a');
 link.className='progress-dependency';
 link.href='#'+progressRowId(parent);
 link.textContent=td.textContent;
 link.addEventListener('click',event=>{
  event.preventDefault();
  const target=$(progressRowId(parent));
  if(!target||progressSelection!==selection)return;
  $('progress-rows').querySelector('.dependency-target')?.classList.remove('dependency-target');
  selection.highlighted=parent;
  target.classList.add('dependency-target');
  target.focus({preventScroll:true});
  target.scrollIntoView({block:'center',inline:'nearest'});
 });
 td.replaceChildren(link);return td;
}
function drawProgress(data){
 const selection=progressSelection;
 const signature=JSON.stringify([data.workflowState,data.tasks]);
 const summary=$('progress-summary');
 if(selection.signature===signature&&summary.className!=='error'){
  $('progress-updated').textContent='최근 조회 '+shortTime(Date.now());
  return;
 }
 selection.signature=signature;
 const tasks=data.tasks, keys=new Map(tasks.map(task=>[task.key,task])),counts={};
 const rows=tasks.map(task=>{
  const state=stateFor(task,keys,data.workflowState);
  counts[state]=(counts[state]||0)+1;
  const row=document.createElement('tr');row.dataset.taskState=state;
  row.id=progressRowId(task.key);row.tabIndex=-1;
  row.classList.toggle('dependency-target',task.key===selection.highlighted);
  const badge=cell(progressLabels[state]||task.status);badge.className='progress-state';
  const label=cell(taskName(task.key));label.title=task.key;
  row.append(label,cell(workerNames[task.workerType]||task.workerType),badge,dependencyCell(task,keys,selection),cell(task.updatedAt==null?'—':date(task.updatedAt)));
  return row;
 });
 summary.className='';
 const nextSummary=tasks.length
 ? '전체 '+tasks.length+'건 · 완료 '+(counts.completed||0)+' · 진행 중 '+(counts.running||0)
   +' · 인수 가능 '+(counts.ready||0)+' · 대기 '+(counts.pending||0)+((counts.failed||counts.blocked)?' · 실패 '+(counts.failed||0)+' · 선행 실패 '+(counts.blocked||0):'')
 : '이 요청에는 저장된 하위 작업이 없습니다.';
 if(summary.textContent!==nextSummary)summary.textContent=nextSummary;
 $('progress-list').hidden=!rows.length;
 const active=document.activeElement,activeRow=active.closest('#progress-rows tr');
 const activeDependency=active.classList.contains('progress-dependency');
 $('progress-rows').replaceChildren(...rows);
 if(activeRow){
  const nextRow=$(activeRow.id);
  (activeDependency?nextRow?.querySelector('.progress-dependency')||nextRow:nextRow)?.focus({preventScroll:true});
 }
 $('progress-updated').textContent='최근 조회 '+shortTime(Date.now());
}
async function checkProgress(){
 const selected=progressSelection;
 if(!selected||!$('progress-dialog').open||progressFetch)return;
 const flight=new AbortController();progressFetch=flight;
 try{
  const query=new URLSearchParams({requestId:selected.id});
  const res=await fetch('/api/request-progress?'+query,{cache:'no-store',signal:AbortSignal.any([flight.signal,AbortSignal.timeout(5500)])});
  if(!res.ok)throw Error('PROGRESS_UNAVAILABLE');
  const data=await res.json();
  if(progressSelection===selected)drawProgress(data);
 }catch{
  if(progressSelection===selected&&!flight.signal.aborted){
   $('progress-summary').className='error';
   $('progress-summary').textContent=$('progress-list').hidden?'진행 상태를 불러오지 못했습니다. 잠시 뒤 다시 확인합니다.':'새 상태를 불러오지 못했습니다. 아래는 마지막으로 확인한 상태입니다.';
  }
 }finally{if(progressFetch===flight)progressFetch=null;}
}
function openProgress(row){
 const modal=$('progress-dialog');
 progressSelection={id:row.officialRequestId};
 const id=row.officialRequestId.replace(/^request\.official\./,'');
 $('progress-target').textContent=(row.targetName||row.targetNpcId||'공식 요청')+' · '+id.slice(0,8)+'…'+id.slice(-6);
 $('progress-target').title=row.officialRequestId;
 $('progress-rows').replaceChildren();$('progress-list').hidden=true;$('progress-updated').textContent='';
 $('progress-summary').className='';$('progress-summary').textContent='하위 작업을 불러오는 중…';
 modal.showModal();
 void checkProgress();
 progressTimer=setInterval(()=>{if(!document.hidden)void checkProgress()},4000);
}
const serviceLabels={connected:'연결됨',warning:'경고',stop:'중지'};
const shortTime=value=>new Date(value).toLocaleTimeString('ko-KR',{hour12:false});
function serviceCard({serverName,state,missed,lastReceivedAt}){
 const item=document.createElement('li'),name=document.createElement('strong');
 const badge=document.createElement('span'),detail=document.createElement('small');
 name.textContent=serverName;
 badge.className='service-state';badge.dataset.state=state;badge.textContent=serviceLabels[state];
 detail.textContent='최근 수신 '+shortTime(lastReceivedAt)+(missed>=2?' · '+missed+'회 누락':'');
 item.append(name,badge,detail);return item;
}
let checkingServices=false;
async function checkServices(){
 if(checkingServices)return;
 checkingServices=true;
 try{
  const res=await fetch('/api/service-status',{cache:'no-store',signal:AbortSignal.timeout(6500)});
  if(!res.ok)throw Error('STATUS_UNAVAILABLE');
  const data=await res.json();
  $('service-rows').replaceChildren(...data.services.map(serviceCard));
  $('service-empty').textContent=data.services.length?'':'헬스체크를 받은 서버가 없습니다.';
  $('service-checked-at').textContent='화면 갱신 '+shortTime(data.checkedAt);
 }catch{
  $('service-rows').replaceChildren();
  $('service-empty').textContent='서버 상태를 확인할 수 없습니다.';
  $('service-checked-at').textContent='';
 }finally{checkingServices=false;}
}
let checkingAlarms=false;
async function checkAlarms(){
 if(checkingAlarms)return;
 const notice=$('alarm-status'),list=$('alarm-list'),rows=$('alarm-rows');
 checkingAlarms=true;$('refresh').disabled=true;
 notice.className='';notice.textContent='알람을 불러오는 중…';
 try{
  const res=await fetch('/api/alarms',{cache:'no-store',signal:AbortSignal.timeout(5500)});
  if(!res.ok)throw Error('UNAVAILABLE');const data=await res.json();
  rows.replaceChildren(...data.alarms.map(alarm=>{
   const row=document.createElement('tr'),target=cell('');
   const id=alarm.officialRequestId,short=id.replace(/^request\.official\./,'');
   const label=short.slice(0,8)+'…'+short.slice(-6);
   if(alarm.hasRequest){
    const link=document.createElement('a');
    link.href='/requests?'+new URLSearchParams({requestId:id});link.textContent='요청 상세 '+label;link.title=id;
    target.append(link);
   }else{target.textContent=label;target.title=id;}
   row.append(cell(taskName(alarm.key)),cell(workerNames[alarm.workerType]||alarm.workerType),
              cell(alarm.retryCount+'회'),cell(date(alarm.updatedAt)),target);
   return row;
  }));
  list.hidden=!data.alarms.length;
  notice.textContent=data.alarms.length?(data.hasMore?'최근 ':'')+data.alarms.length+'건 표시':'재시도 한도에 도달한 실패 작업이 없습니다.';
 }catch{
  rows.replaceChildren();list.hidden=true;notice.className='error';notice.textContent='알람을 불러오지 못했습니다.';
 }finally{checkingAlarms=false;$('refresh').disabled=false;}
}
if($('rows')){
 $('progress-close').onclick=()=>$('progress-dialog').close();
 $('progress-dialog').addEventListener('close',()=>{
  progressSelection=null;
  progressFetch?.abort();progressFetch=null;
  clearInterval(progressTimer);progressTimer=null;
 });
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)void checkProgress()});
 $('status-filter').addEventListener('change',event=>{status=event.target.value;stack=[null];load()});
 $('refresh').onclick=()=>{stack=[null];load()};
 $('prev').onclick=()=>{if(stack.length>1){stack.pop();load()}};
 $('next').onclick=()=>{if(cursor){stack.push(cursor);load()}};
 load();
 const linkedId=new URLSearchParams(location.search).get('requestId');
 if(linkedId&&/^request\.official\.[a-f0-9]{64}$/.test(linkedId))openProgress({officialRequestId:linkedId});
}else if($('alarm-rows')){
 $('refresh').onclick=checkAlarms;
 void checkAlarms();
}else if($('service-rows')){
 $('refresh').onclick=checkServices;
 checkServices();
 setInterval(()=>{if(!document.hidden)checkServices()},5000);
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)checkServices()});
}
