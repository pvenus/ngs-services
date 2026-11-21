'use strict';
const $=id=>document.getElementById(id);
const api='/api/deployments', notice=$('deploy-status'), gate=$('deploy-gate'), details=$('deploy-details');
const unlock=$('deploy-unlock'), key=$('deploy-owner-key'), apply=$('deploy-apply');
const states={not_prepared:'준비된 커밋 없음',not_configured:'배포 경로 미설정',ready:'배포 준비 완료',
 running:'배포 중',deployed:'배포 완료 · 상태 확인됨',failed:'실패 · 결과 확인 필요',unknown:'상태 불명 · 자동 재시도하지 않음'};
let current=null,timer=null,active=false;
function status(text){notice.textContent=text;}
function render(value){
 current=value;
 gate.hidden=true;details.hidden=false;
 $('deploy-sha').textContent=value.sha??'—';
 $('deploy-verified').textContent=value.verifiedAt?new Date(value.verifiedAt).toLocaleString('ko-KR'):'—';
 $('deploy-mac').textContent=states[value.mac]??'확인 불가';
 $('deploy-space').textContent=value.space==='pending'?'게시 대기 · 별도 게시 필요':'게시 경로 미설정';
 apply.disabled=active||value.canDeploy!==true;
}
async function reload(){
 clearTimeout(timer);
 try{
  const r=await fetch(api,{credentials:'same-origin',cache:'no-store'});
  if(r.status===401){current=null;details.hidden=true;gate.hidden=false;status('Mac 소유자 확인이 필요합니다.');return;}
  if(r.status===503){current=null;gate.hidden=true;details.hidden=true;status('배포 기능이 구성되지 않았습니다.');return;}
  if(!r.ok)throw Error('UNAVAILABLE');
  const data=await r.json();render(data);status(data.mac==='running'?'상태를 자동으로 확인 중입니다.':'');
  if(data.mac==='running')timer=setTimeout(reload,2000);
 }catch{
  current=null;apply.disabled=true;status('배포 상태를 확인하지 못했습니다. 재실행 대신 새로고침으로 결과를 확인해 주세요.');
 }
}
async function login(){
 if(unlock.disabled)return;
 const ownerKey=key.value.trim();key.value='';
 if(!/^[a-f0-9]{64}$/.test(ownerKey)){status('Mac 소유자 확인 코드를 입력해 주세요.');return;}
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
apply.addEventListener('click',async()=>{
 if(active||!current?.canDeploy)return;
 const {sha,csrf}=current;active=true;apply.disabled=true;
 status('검증된 커밋의 Mac 배포를 요청하고 있습니다.');
 try{
  const r=await fetch(api,{method:'POST',credentials:'same-origin',cache:'no-store',headers:{
   'Content-Type':'application/json','X-OPS-Deploy-CSRF':csrf},
   body:JSON.stringify({action:'deploy',sha})});
  if(r.status!==202){status('배포를 접수하지 못했습니다. 현재 상태를 다시 확인해 주세요.');return;}
  await reload();
 }catch{status('응답을 받지 못했습니다. 새로고침해서 배포 상태를 확인해 주세요.');}
 finally{active=false;}
});
void reload();
