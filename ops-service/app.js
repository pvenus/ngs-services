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
   if(r.status==='failed'&&r.failureReason==='REQUEST_UNSUPPORTED_RECREATE'){
    const reason=document.createElement('small');reason.textContent='이 버전에서 지원하지 않는 요청입니다. 새로 요청해 주세요.';state.append(reason);
   }
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
const taskNames={audio_generate:'BGM 생성',audio_separate:'음원 분리',delete:'기존 NPC 폴더 삭제',preset:'프리셋 생성',planning_representative:'대표 기획',
 portrait_representative:'대표 포트레이트',planning_idle:'아이들 기획',portrait_idle_reference:'아이들 기준 포트레이트',
 motion_2d_image:'2D 기준 이미지 생성',motion_2d_finalize:'2D 프리셋 최종 등록'};
const directionNames={n:'북',ne:'북동',e:'동',se:'남동',s:'남',sw:'남서',w:'서',nw:'북서'};
function taskName(key){
 if(!key)return '—';
 if(Object.hasOwn(taskNames,key))return taskNames[key];
 const portrait=key.match(/^portrait_(n|ne|e|se|s|sw|w|nw)$/);
 if(portrait)return directionNames[portrait[1]]+' 포트레이트';
 const motion=key.match(/^(animation|frames)_(walk|run)_(n|ne|e|se|s|sw|w|nw)$/);
 if(motion)return directionNames[motion[3]]+' '+(motion[2]==='walk'?'걷기':'뛰기')+(motion[1]==='animation'?' 원본 PNG':' 프레임 후처리');
 const motion2D=key.match(/^motion_2d_(?:(frames|encode)_)?(n|ne|e|se|s|sw|w|nw)$/);
 if(motion2D)return directionNames[motion2D[2]]+' '+(motion2D[1]==='frames'?'PNG 프레임 생성':motion2D[1]==='encode'?'MP4 인코딩':'2D 영상 생성');
 return key;
}
function motionFinalDependencies(task,keys){
 if(task.key!=='motion_2d_finalize'||task.type!=='motion-preset.convert-2d.finalize'||task.dependsOn!==null)return null;
 const split=task.workerType==='postprocess';
 const type='motion-preset.convert-2d.'+(split?'encode':'clip'), worker=split?'postprocess':'animation';
 if(task.workerType!==worker)return null;
 return Object.keys(directionNames).map(direction=>{
  const parent=keys.get('motion_2d_'+(split?'encode_':'')+direction);
  return parent?.type===type&&parent.workerType===worker?parent:null;
 });
}
function stateFor(task,keys,state){
 if(task.status!=='pending')return task.status;
 if(state!=='planned')return 'pending';
 const finalParents=motionFinalDependencies(task,keys);
 if(finalParents){
  if(finalParents.some(parent=>parent?.status==='failed'))return 'blocked';
  return finalParents.every(parent=>parent?.status==='completed')?'ready':'pending';
 }
 const parents=task.dependencies??(task.dependsOn?[task.dependsOn]:[]);
 if(parents.some(key=>keys.get(key)?.status==='failed'))return 'blocked';
 return parents.every(key=>keys.get(key)?.status==='completed')?'ready':'pending';
}
const progressLabels={completed:'완료',running:'진행 중',ready:'인수 가능',pending:'대기',failed:'실패',blocked:'선행 실패'};
const progressRowId=key=>'progress-task-'+encodeURIComponent(key);
function dependencyCell(task,keys,selection){
 if(motionFinalDependencies(task,keys))return cell('8방향 MP4');
 const parents=task.dependencies??(task.dependsOn?[task.dependsOn]:[]);
 const td=cell(parents.length?'':taskName(null));
 for(const [index,parent] of parents.entries()){
  if(index)td.append(document.createTextNode(' · '));
  if(!keys.has(parent)){td.append(document.createTextNode(taskName(parent)));continue;}
  const link=document.createElement('a');
  link.className='progress-dependency';
  link.href='#'+progressRowId(parent);
  link.textContent=taskName(parent);
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
  td.append(link);
 }
 return td;
}
function retryLabel({retryCount,retryLimit}){
 if(!Number.isSafeInteger(retryCount)||retryCount<0)return '—';
 return Number.isSafeInteger(retryLimit)&&retryLimit>=0?retryCount+' / '+retryLimit:retryCount+'회';
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
  row.append(label,cell(workerNames[task.workerType]||task.workerType),badge,cell(retryLabel(task)),dependencyCell(task,keys,selection),cell(task.updatedAt==null?'—':date(task.updatedAt)));
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
function serviceCard({serverName,state,missed,lastReceivedAt,commitSha}){
 const item=document.createElement('li'),name=document.createElement('strong');
 const badge=document.createElement('span'),detail=document.createElement('small');
 const observation=serverName==='ngs-local'||serverName==='ngs-space';
 name.textContent=serverName==='document-worker'?'문서 워커 1':serverName==='document-worker-2'?'문서 워커 2':serverName==='image-worker'?'이미지 워커 1':/^image-worker-[234]$/.test(serverName)?'이미지 워커 '+serverName.at(-1):observation?(serverName==='ngs-local'?'NGS Local':'NGS Space'):serverName;
 badge.className='service-state';badge.dataset.state=state;badge.textContent=serviceLabels[state];
 detail.textContent=(observation?'마지막 연락 ':'최근 수신 ')+
  (observation?new Date(lastReceivedAt).toLocaleString('ko-KR',{hour12:false}):shortTime(lastReceivedAt))+
  (missed>=2?' · '+missed+'회 누락':'');
 item.append(name,badge,detail);
 if(observation||serverName==='workflow-planner'){
  const version=document.createElement('small');version.className='ngs-observation-version';
  version.textContent=commitSha?'커밋 '+commitSha:'커밋 확인 안 됨';
  item.append(version);
 }
 return item;
}
const serviceGroupLabels={ngs:'NGS',services:'서비스',workers:'워커'};
function serviceGroups(rows){
 const byGroup={ngs:[],services:[],workers:[]};
 for(const row of rows){
  const name=row.serverName;
  const group=name==='ngs-local'||name==='ngs-space'?'ngs':
   (name.endsWith('-worker')||name.startsWith('worker-')||/^(?:image-worker-[234]|document-worker-2)$/.test(name))?'workers':'services';
  byGroup[group].push(row);
 }
 return Object.entries(byGroup).filter(([,items])=>items.length).map(([group,items])=>{
  const section=document.createElement('section'),title=document.createElement('h2'),list=document.createElement('ul');
  section.className='deploy-group';section.dataset.group=group;
  title.textContent=serviceGroupLabels[group];
  list.append(...items.map(serviceCard));section.append(title,list);
  return section;
 });
}
let checkingServices=false;
async function checkServices(){
 if(checkingServices)return;
 checkingServices=true;
 try{
  const res=await fetch('/api/service-status',{cache:'no-store',signal:AbortSignal.timeout(6500)});
  if(!res.ok)throw Error('STATUS_UNAVAILABLE');
  const data=await res.json();
  $('service-rows').replaceChildren(...serviceGroups(data.services));
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
