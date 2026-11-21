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
 services:{label:'서비스',targets:[['request-service','RS'],['rs-mcp','RS-MCP'],['workflow-planner','Workflow Planner'],['backend-stack','백엔드 스택 · PDS 포함']]},
 workers:{label:'워커',targets:[['worker-document','문서'],['worker-file','파일'],['worker-image','이미지'],
  ['worker-animation','애니메이션'],['worker-postprocess','후처리'],['worker-audio','음악']]},
};
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
 const comparison=value.comparison===null&&!inProgress&&local&&space?
  (local.sha===space.sha?'match':'mismatch'):value.comparison;
 if(!local||!space||!['match','mismatch'].includes(comparison)){
  compareNode.textContent='로컬·스페이스 양쪽 배포가 확인되면 버전을 비교합니다.';
 }else if(comparison==='mismatch'&&local.sha!==space.sha){
  compareNode.className+=' deploy-mismatch';compareNode.textContent='버전 불일치: 로컬·스페이스에 다른 커밋이 배포돼 있습니다.';
 }else if(comparison==='match'&&local.sha===space.sha){
  compareNode.textContent='버전 일치: 로컬·스페이스에 같은 커밋이 배포돼 있습니다.';
 }else compareNode.textContent='검증된 버전 비교를 확인할 수 없습니다.';
 return compareNode;
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
  if(target==='ngs-space')name.append(node('small','게시 대상: '+spacePublicationHost));
  const stateCell=node('td'),badge=node('span',spaceUnlinked?'자동 게시 미연결':stateLabels[state],'deploy-state');stateCell.append(badge);
  const failure=item?.failure;
  if(state==='failed'&&typeof failure?.code==='string'&&/^TARGET_[A-Z_]+$/u.test(failure.code)&&failure.code.length<60){
   const phases={preflight:'준비 확인',unload:'기존 실행 종료',selector:'코드 교체',start:'시작',health:'상태 확인'};
   const phase=Object.hasOwn(phases,failure.phase)?phases[failure.phase]:'배포';
   const recovery=failure.rollback==='restored'?' · 이전 버전 복구됨':failure.rollback==='failed'?' · 복구 확인 필요':'';
   stateCell.append(node('small',phase+' · '+failure.code+recovery));
  }
  const action=node('td'),button=node('button','배포','deploy-row-action');button.type='button';
  button.setAttribute('aria-label',label+' 배포');button.disabled=active||item?.canDeploy!==true||state==='running'||spaceUnlinked;
  button.addEventListener('click',()=>void deploy(target,button));
  rowButtons.set(target,button);action.append(button);
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
 all.disabled=active||value.canDeployAll!==true||spacePublicationUnlinked(targets.get('ngs-space'));
 if(focusTarget&&!rowButtons.get(focusTarget)?.disabled)rowButtons.get(focusTarget)?.focus();
 return [...targets.values()].some(item=>['running','queued'].includes(item.state));
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
  if(running)timer=setTimeout(reload,2500);
 }catch{
  if(sequence!==readSequence)return;
  current=null;disableActions();status('배포 상태를 확인하지 못했습니다. 새로고침해서 결과를 확인해 주세요.');
 }
}
async function deploy(target,button){
 if(active||!current||button.disabled)return;
 const {value,targets}=current;
 if(target==='all'?value.canDeployAll!==true:targets.get(target)?.canDeploy!==true)return;
 clearTimeout(timer);readSequence++;active=true;disableActions();
 status(target==='all'?'모든 대상의 배포를 요청하는 중입니다.':'선택한 대상의 배포를 요청하는 중입니다.');
 try{
  const r=await fetch(api,{method:'POST',credentials:'same-origin',cache:'no-store',headers:{
   'Content-Type':'application/json','X-OPS-Deploy-CSRF':value.csrf},
   body:JSON.stringify({action:'wake',target})});
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
