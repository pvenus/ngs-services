'use strict';
const $=id=>document.getElementById(id);
const api='/api/deployments',notice=$('deploy-status'),gate=$('deploy-gate'),details=$('deploy-details'),groupsNode=$('deploy-groups');
const unlock=$('deploy-unlock'),key=$('deploy-owner-key'),all=$('deploy-all');
const stateLabels={
 not_prepared:'검증된 후보 없음',not_configured:'배포 경로 미설정',blocked:'차단됨',pending:'후보 검증 대기',queued:'배포 대기',
 ready:'배포 준비',running:'배포 중',deployed:'배포 완료',current:'최신 상태',
 failed:'실패 · 확인 필요',unknown:'상태 확인 필요',
};
const groupLayout={
 ngs:{label:'NGS',targets:[['ngs-local','로컬'],['ngs-space','스페이스']]},
 services:{label:'서비스',targets:[['request-service','RS'],['rs-mcp','RS-MCP'],['workflow-planner','Workflow Planner'],['filex-writer','Filex 저장기'],['backend-stack','백엔드 스택 · PDS 포함']]},
 workers:{label:'워커',targets:[['worker-document','문서'],['worker-file','파일'],['worker-image','이미지'],
  ['worker-animation','애니메이션'],['worker-postprocess','후처리'],['worker-audio','음악']]},
};
const preparationLabels={ready:'후보 검증 완료',current:'현재 배포와 동일',equivalent:'현재 배포와 동일',
 pending:'GitHub 빌드 대기',blocked:'후보 준비 차단',failed:'후보 준비 실패',in_progress:'후보 검증 중',
 auth_required:'GitHub 읽기 권한 확인 필요',not_configured:'후보 준비 경로 미설정'};
function preparationText(item){
 if(!item||!Object.hasOwn(preparationLabels,item.state))return '';
 const sha=typeof item.sha==='string'&&item.sha.length===40&&shaPattern.test(item.sha)?' · '+item.sha.slice(0,7):'';
 const code=typeof item.code==='string'&&/^[A-Z][A-Z0-9_]{1,79}$/u.test(item.code)?' · '+item.code:'';
 return preparationLabels[item.state]+sha+code;
}
const spacePublicationHost='vinus-npcgen.park-venus0518.chatgpt.site';
const spacePublicationUnlinked=target=>target?.state==='not_configured';
const shaPattern=/^[a-f0-9]{40}$/u,keyPattern=/^[a-f0-9]{64}$/u;
const stampFormat=new Intl.DateTimeFormat('ko-KR',{dateStyle:'medium',timeStyle:'medium'});
let current=null,timer=null,active=false,readSequence=0,rowButtons=new Map();
function status(text){notice.textContent=text;}
function node(tag,text,style){
 const item=document.createElement(tag);
 if(text!==undefined)item.textContent=text;
 if(style)item.className=style;
 return item;
}
function evidence(item){
 if(!item||typeof item.sha!=='string'||item.sha.length!==40||!shaPattern.test(item.sha)||
  typeof item.deployedAt!=='string'||!/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/u.test(item.deployedAt))return null;
 const time=Date.parse(item.deployedAt);
 if(!Number.isFinite(time)||new Date(time).toISOString()!==item.deployedAt.slice(0,-1)+'.000Z')return null;
 return {sha:item.sha,time,stamp:item.deployedAt};
}
function evidenceCell(value){
 const cell=node('td'),observed=evidence(value);
 if(!observed){cell.textContent='확인된 배포 없음';return cell;}
 const sha=node('code',observed.sha.slice(0,12));sha.title=observed.sha;sha.setAttribute('aria-label','커밋 '+observed.sha);
 const time=node('time',stampFormat.format(observed.time));time.dateTime=observed.stamp;
 cell.append(sha,time);return cell;
}
function compare(value,targets){
 const compareNode=node('p',undefined,'deploy-comparison');
 compareNode.setAttribute('aria-live','polite');
 const localRow=targets.get('ngs-local'),spaceRow=targets.get('ngs-space');
 const local=evidence(localRow?.observed),space=evidence(spaceRow?.observed);
 const inProgress=[localRow,spaceRow].some(row=>['running','queued','failed','unknown'].includes(row?.state));
 const comparison=inProgress?'unknown':value.sourceComparison;
 if(!local||!space){
  compareNode.textContent='로컬·스페이스 양쪽 배포가 확인되면 소스를 비교합니다.';
 }else if(local.sha===space.sha&&!inProgress){
  compareNode.textContent='버전 일치: 로컬·스페이스에 같은 커밋이 배포돼 있습니다.';
 }else if(comparison==='same-local-source'){
  compareNode.textContent='로컬 코드 일치: 배포 커밋은 다르지만 로컬에 쓰는 코드는 같습니다.';
 }else if(comparison==='different-local-source'){
  compareNode.className+=' deploy-mismatch';
  compareNode.textContent='로컬 코드 차이: 두 배포에 포함된 로컬 소스가 다릅니다.';
 }else compareNode.textContent='로컬 소스의 일치 여부를 아직 확인하지 못했습니다.';
 return compareNode;
}
const threadUrlPattern=/^(?:https:\/\/chatgpt\.com\/c\/|codex:\/\/threads\/)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![\s\S])/u;
const threadIdPattern=/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}(?![\s\S])/u;
function spaceHandoff(item){
 const detail=item?.publication?.handoff;
 if(item?.publication?.provider!=='chatgpt-sites'||item.publication.hostname!==spacePublicationHost||!detail||
  (Object.hasOwn(detail,'sha')&&(typeof detail.sha!=='string'||detail.sha.length!==40||!shaPattern.test(detail.sha))))return null;
 if(Object.hasOwn(detail,'url')&&(typeof detail.url!=='string'||!threadUrlPattern.test(detail.url)))return null;
 if(!detail.url&&(typeof detail.threadId!=='string'||!threadIdPattern.test(detail.threadId)||detail.title!=='비나리 스페이스 배포'))return null;
 return {url:detail.url,sha:detail.sha,title:detail.title==='비나리 스페이스 배포'?detail.title:'배포 스레드'};
}
async function openSpaceHandoff(handoff,fallback){
 const text=handoff.sha?'NGS Space를 pvenus/ngs-platform 커밋 '+handoff.sha+'로 게시해 주세요. 게시 대상: '+spacePublicationHost+'.':
  '비나리 스페이스 최신 검증 버전을 배포해줘.';
 // Open in the click gesture; clipboard availability does not determine whether the thread opens.
 if(handoff.url)window.open(handoff.url,'_blank','noopener,noreferrer');
 try{
  await navigator.clipboard.writeText(text);
  status('배포 요청 문구를 복사했습니다. '+handoff.title+'에서 붙여넣어 직접 전송해 주세요.');
 }catch{
  fallback.value=text;fallback.hidden=false;fallback.focus();fallback.select();
  status('아래 요청 문구를 복사해 배포 스레드에서 직접 전송해 주세요.');
 }
}
const publicationLabels={pending:'게시 요청 대기',claimed:'게시 작업 중',succeeded:'게시 완료',failed:'게시 실패',uncertain:'게시 결과 확인 필요'};
const isSha=sha=>typeof sha==='string'&&sha.length===40&&shaPattern.test(sha);
function spacePublication(item){
 const publication=item?.publication;
 if(publication?.provider!=='chatgpt-sites'||publication.hostname!==spacePublicationHost)return null;
 const candidate=isSha(publication.candidateSha)?publication.candidateSha:null;
 const request=publication.request;
 const latest=request&&Object.hasOwn(publicationLabels,request.state)&&
  typeof request.requestId==='string'&&/^[a-zA-Z0-9._-]{1,128}(?![\s\S])/u.test(request.requestId)&&
  evidence({sha:request.sha,deployedAt:request.requestedAt})?request:null;
 return {candidate,latest,configured:typeof publication.canRequest==='boolean',canRequest:publication.canRequest===true,
  busy:['pending','claimed'].includes(latest?.state),blocked:latest?.state==='uncertain'};
}
function publicationStatus(cell,publication){
 if(!publication)return;
 if(publication.candidate){
  const candidate=node('small','게시 후보: '+publication.candidate);
  candidate.className='deploy-space-candidate';cell.append(candidate);
 }
 const request=publication.latest;
 if(!request)return;
 const state=node('small',publicationLabels[request.state]);state.dataset.publicationState=request.state;
 const sha=node('code',request.sha.slice(0,12));sha.title=request.sha;
 const requested=node('time',stampFormat.format(Date.parse(request.requestedAt)));requested.dateTime=request.requestedAt;
 cell.append(state,node('small','요청 ID: '+request.requestId),sha,requested);
}
const ngsFailurePhases=Object.freeze({
 validate_candidate:'배포 후보 확인',check_current:'기존 버전 상태 확인',stop_current:'기존 실행 종료',
 wait_ports:'기존 포트 해제 확인',switch_selector:'코드 교체',start_candidate:'새 버전 시작',
 check_candidate:'새 버전 상태 확인',check_storage:'저장소 확인',record_result:'결과 기록',
});
const ngsRollbackPhases=Object.freeze({
 rollback_stop:'새 버전 종료',rollback_restore:'이전 코드 복원',rollback_start:'이전 버전 시작',rollback_check:'복구 상태 확인',
});
const ngsSafeCode=code=>typeof code==='string'&&/^[A-Z][A-Z0-9_]{3,59}$/u.test(code);
function localFailureStatus(cell,failure){
 if(!failure||!['not_attempted','restored','failed','unknown'].includes(failure.rollback))return;
 const stage=Object.hasOwn(ngsFailurePhases,failure.phase)?ngsFailurePhases[failure.phase]:null;
 const code=ngsSafeCode(failure.code)&&failure.code!=='NGS_LOCAL_CAUSE_NOT_RECORDED'?failure.code:null;
 cell.append(node('small',[stage,code??'원인 미기록'].filter(Boolean).join(' · ')));
 if(failure.rollback==='restored')cell.append(node('small','이전 버전 복구 확인'));
 else if(failure.rollback==='failed'){
  const detail=failure.rollbackFailure;
  const where=Object.hasOwn(ngsRollbackPhases,detail?.stage)?ngsRollbackPhases[detail.stage]:null;
  cell.append(node('small',['이전 버전 복구 미확인',where,where&&ngsSafeCode(detail?.code)?detail.code:null].filter(Boolean).join(' · ')));
 }else if(failure.rollback==='not_attempted')cell.append(node('small','복구 실행 안 함'));
 else cell.append(node('small','복구 결과 미기록'));
}
function localRetry(item){
 const result=item?.retry;
 if(item?.state!=='failed'||!isSha(result?.sha)||result.sha!==item.candidateSha)return null;
 const previous=result.previousAttemptId;
 if(previous!=='initial'&&!(typeof previous==='string'&&/^(?!0{32}$)[a-f0-9]{32}$/u.test(previous)&&previous.length===32))return null;
 return result;
}
function groupSection(id,layout,value,targets){
 const section=node('section',undefined,'deploy-group');
 section.dataset.group=id;section.setAttribute('aria-labelledby','deploy-heading-'+id);
 const heading=node('h2',layout.label);heading.id='deploy-heading-'+id;section.append(heading);
 if(id==='ngs')section.append(compare(value,targets));
 const scroll=node('div',undefined,'table-scroll');
 const table=node('table'),head=node('thead'),body=node('tbody'),columns=node('tr');
 for(const name of ['대상','상태','마지막 확인 배포','실행']){
  const col=node('th',name);col.scope='col';columns.append(col);
 }
 head.append(columns);table.append(head,body);scroll.append(table);section.append(scroll);
 for(const [target,label] of layout.targets){
  const item=targets.get(target),state=Object.hasOwn(stateLabels,item?.state)?item.state:'unknown';
  const row=node('tr');row.dataset.deployTarget=target;row.dataset.deployState=state;
  const name=node('th',label);name.scope='row';if(target==='backend-stack')name.append(node('small','PDS와 연결된 백엔드 서비스가 함께 교체됩니다.'));
  const spaceUnlinked=target==='ngs-space'&&spacePublicationUnlinked(item);
  if(target==='ngs-space'){
   const host=item?.publication?.provider==='chatgpt-sites'&&item.publication.hostname===spacePublicationHost?
    item.publication.hostname:spacePublicationHost;
   name.append(node('small','게시 대상: '+host));
   const live=item?.publication?.live;
   if(live){
    const liveState={connected:'응답 확인',warning:'응답 지연',stop:'응답 없음'}[live.state];
    if(typeof liveState==='string'){
     const commit=typeof live.commitSha==='string'&&live.commitSha.length===40&&shaPattern.test(live.commitSha)?live.commitSha.slice(0,12):'미확인';
     name.append(node('small','NGS-MCP: '+liveState+' · 현재 커밋 '+commit));
    }
   }
  }
  const stateCell=node('td'),badge=node('span',spaceUnlinked?'자동 게시 미연결':stateLabels[state],'deploy-state');stateCell.append(badge);
  const prepare=preparationText(value.preparation?.targets?.[target]);
  if(prepare)stateCell.append(node('small',prepare));
  const failure=item?.failure;
  if(state==='failed'&&target==='ngs-local')localFailureStatus(stateCell,failure);
  else if(state==='failed'&&typeof failure?.code==='string'&&/^TARGET_[A-Z_]+$/u.test(failure.code)&&failure.code.length<60){
   const phases={preflight:'준비 확인',unload:'기존 실행 종료',selector:'코드 교체',start:'시작',health:'상태 확인'};
   const phase=Object.hasOwn(phases,failure.phase)?phases[failure.phase]:'배포';
   const recovery=failure.rollback==='restored'?' · 이전 버전 복구됨':failure.rollback==='failed'?' · 복구 확인 필요':'';
   stateCell.append(node('small',phase+' · '+failure.code+recovery));
  }
  const publication=target==='ngs-space'?spacePublication(item):null;
  publicationStatus(stateCell,publication);
  const queue=spaceUnlinked&&Boolean(publication?.configured||publication?.latest);
  const handoff=spaceUnlinked&&!queue?spaceHandoff(item):null;
  const retry=target==='ngs-local'?localRetry(item):null;
  const action=node('td'),button=node('button',retry?'같은 버전 다시 배포':queue?'스페이스 게시 요청':handoff?(handoff.url?'배포 스레드에 전달':'배포 요청 복사'):'배포','deploy-row-action');button.type='button';
  button.setAttribute('aria-label',retry?'로컬 같은 버전 다시 배포':queue?'스페이스 게시 요청':handoff?'배포 스레드에 전달 (직접 전송)':label+' 배포');
  button.disabled=active||(!retry&&(queue?(!publication.canRequest||!publication.candidate||publication.busy||publication.blocked):(!handoff&&(item?.canDeploy!==true||state==='running'||spaceUnlinked))));
  rowButtons.set(target,button);action.append(button);
  if(retry)button.addEventListener('click',()=>void retryLocal(button,retry));
  else if(queue)button.addEventListener('click',()=>void queueSpace(button));
  else if(handoff){
   if(!handoff.url)action.append(node('small','전송 대상: '+handoff.title));
   const fallback=node('textarea');fallback.readOnly=true;fallback.hidden=true;
   fallback.setAttribute('aria-label','복사할 배포 요청 문구');action.append(fallback);
   button.addEventListener('click',()=>{if(!button.disabled)void openSpaceHandoff(handoff,fallback)});
  }else button.addEventListener('click',()=>void deploy(target,button));
  row.append(name,stateCell,evidenceCell(item?.observed),action);body.append(row);
 }
 return section;
}
function acceptTargets(value,observed){
 if(!value||!Array.isArray(value.groups)||!Array.isArray(value.targets)||value.groups.length>8||value.targets.length>64)throw Error('INVALID_STATUS');
 const targets=new Map(),duplicate=new Set();
 for(const group of value.groups){
  if(!group||typeof group.id!=='string'||!Object.hasOwn(groupLayout,group.id)||!Array.isArray(group.targets))continue;
  if(duplicate.has(group.id))throw Error('INVALID_STATUS');
  duplicate.add(group.id);
  const allowed=new Set(groupLayout[group.id].targets.map(([id])=>id));
  for(const item of value.targets){
   if(!item||item.group!==group.id||!allowed.has(item.id)||!group.targets.includes(item.id))continue;
   if(targets.has(item.id))throw Error('INVALID_STATUS');
   const previous=item.id==='ngs-local'?observed?.local:item.id==='ngs-space'?observed?.space:null;
   targets.set(item.id,{...item,observed:evidence(item.observed)?item.observed:evidence(previous)?previous:null});
  }
 }
 return targets;
}
function disableActions(){all.disabled=true;for(const button of rowButtons.values())button.disabled=true;}
function render(response){
 if(typeof response?.csrf!=='string'||response.csrf.length!==64||!keyPattern.test(response.csrf))throw Error('INVALID_STATUS');
 const value={...response.cycle,csrf:response.csrf};
 const targets=acceptTargets(value,response.observed);
 const focusTarget=[...rowButtons].find(([,button])=>document.activeElement===button)?.[0];
 current={value,targets};rowButtons=new Map();
 gate.hidden=true;details.hidden=false;
 groupsNode.replaceChildren(...Object.entries(groupLayout).map(([id,layout])=>groupSection(id,layout,value,targets)));
 all.disabled=active||value.canDeployAll!==true;
 if(focusTarget&&!rowButtons.get(focusTarget)?.disabled)rowButtons.get(focusTarget)?.focus();
 return [...targets.values()].some(item=>['running','queued'].includes(item.state))||spacePublication(targets.get('ngs-space'))?.busy===true;
}
async function reload(){
 clearTimeout(timer);
 const sequence=++readSequence;
 try{
  const r=await fetch(api,{credentials:'same-origin',cache:'no-store'});
  if(sequence!==readSequence)return;
  if(r.status===401){current=null;disableActions();details.hidden=true;gate.hidden=false;status('Mac 소유자 확인이 필요합니다.');return;}
  if(r.status===503){current=null;disableActions();gate.hidden=true;details.hidden=true;status('배포 기능이 구성되지 않았습니다.');return;}
  if(!r.ok)throw Error('UNAVAILABLE');
  const data=await r.json();if(sequence!==readSequence)return;
  const running=render(data);
  status(running?'대기 또는 배포 중인 대상의 상태를 자동으로 확인합니다.':'');
  timer=setTimeout(reload,running?2500:30000);
 }catch{
  if(sequence!==readSequence)return;
  current=null;disableActions();status('배포 상태를 확인하지 못했습니다. 새로고침해서 결과를 확인해 주세요.');
 }
}
async function queueSpace(button){
 if(active||!current||button.disabled)return;
 const item=current.targets.get('ngs-space'),publication=spacePublication(item);
 if(!spacePublicationUnlinked(item)||!publication?.canRequest||!publication.candidate||publication.busy||publication.blocked)return;
 await submitDeployment({action:'queue-space',target:'ngs-space',sha:publication.candidate},
  '스페이스 게시 요청을 접수하는 중입니다.');
}
async function retryLocal(button,shown){
 if(active||!current||button.disabled)return;
 const retry=localRetry(current.targets.get('ngs-local'));
 if(!retry||retry.sha!==shown.sha||retry.previousAttemptId!==shown.previousAttemptId)return;
 await submitDeployment({action:'retry-local',target:'ngs-local',sha:retry.sha,expectedPreviousAttemptId:retry.previousAttemptId},
  '확인된 같은 로컬 버전의 재배포를 요청하는 중입니다.');
}
async function deploy(target,button){
 if(active||!current||button.disabled)return;
 const {value,targets}=current;
 if(target==='all'?value.canDeployAll!==true:targets.get(target)?.canDeploy!==true)return;
 await submitDeployment({action:'wake',target},
  target==='all'?'배포 가능한 대상만 요청하는 중입니다.':'선택한 대상의 배포를 요청하는 중입니다.');
}
async function submitDeployment(payload,message){
 const csrf=current.value.csrf;
 clearTimeout(timer);readSequence++;active=true;disableActions();status(message);
 try{
  const r=await fetch(api,{method:'POST',credentials:'same-origin',cache:'no-store',headers:{
   'Content-Type':'application/json','X-OPS-Deploy-CSRF':csrf},
   body:JSON.stringify(payload)});
  if(r.status===401){active=false;await reload();return;}
  if(r.status!==202){status('배포를 접수하지 못했습니다. 새로고침한 뒤 상태를 확인해 주세요.');return;}
  active=false;await reload();
 }catch{status('배포 응답을 확인하지 못했습니다. 다시 실행하기 전에 새로고침해서 상태를 확인해 주세요.');}
 finally{active=false;}
}
async function login(){
 if(unlock.disabled)return;
 const ownerKey=key.value.trim();key.value='';
 if(ownerKey.length!==64||!keyPattern.test(ownerKey)){status('Mac 소유자 확인 코드를 입력해 주세요.');return;}
 unlock.disabled=true;
 try{
  const r=await fetch(api+'/session',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({ownerKey})});
  if(!r.ok){status(r.status===403?'소유자 확인에 실패했습니다.':'배포 기능을 사용할 수 없습니다.');return;}
  await reload();
 }catch{status('소유자 확인을 완료하지 못했습니다.');}
 finally{unlock.disabled=false;}
}
unlock.addEventListener('click',login);
key.addEventListener('keydown',event=>{if(event.key==='Enter')void login();});
$('deploy-refresh').addEventListener('click',reload);
all.addEventListener('click',()=>void deploy('all',all));
void reload();
