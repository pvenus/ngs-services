const state={runs:[],selected:null,trace:null,status:"",timer:null};
const $=id=>document.getElementById(id);
const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const time=value=>value?new Intl.DateTimeFormat("ko-KR",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}).format(new Date(value)):"-";
const statusName=value=>({active:"실행 중",running:"실행 중",completed:"완료",failed:"실패","awaiting-confirmation":"승인 대기"}[value]||value||"알 수 없음");

async function api(url){const response=await fetch(url,{cache:"no-store"});if(!response.ok)throw new Error(`${response.status} ${response.statusText}`);return response.json()}
function setConnection(ok){$("liveDot").className=`dot ${ok?"online":"offline"}`;$("connectionText").textContent=ok?"서비스 연결됨":"연결 끊김"}

async function loadRuns(){
  try{
    const query=new URLSearchParams({limit:"200"});if(state.status)query.set("status",state.status);
    const data=await api(`/v1/tracking/workflows?${query}`);state.runs=data.items||[];setConnection(true);renderRuns();
    if(state.selected){const exists=state.runs.find(run=>run.id===state.selected);if(exists)await loadTrace(state.selected,false)}
  }catch(error){setConnection(false);$("runCount").textContent=`오류: ${error.message}`}
}
function renderRuns(){
  $("runCount").textContent=`${state.runs.length}개 실행`;
  $("runList").innerHTML=state.runs.map(run=>`<article class="run-card ${run.id===state.selected?"selected":""}" data-id="${esc(run.id)}">
    <div class="run-top"><i class="status-dot ${esc(run.status)}"></i><div class="run-title">${esc(run.workflowType||run.id)}</div></div>
    <div class="run-goal">${esc(run.goal||"실행 목표 없음")}</div>
    <div class="run-meta"><span>${statusName(run.status)}</span><span>${time(run.updatedAt)}</span></div>
  </article>`).join("")||'<div class="console-placeholder">조건에 맞는 실행이 없습니다.</div>';
  document.querySelectorAll(".run-card").forEach(card=>card.onclick=()=>selectRun(card.dataset.id));
}
async function selectRun(id){state.selected=id;renderRuns();$("emptyState").hidden=true;$("workflowDetail").hidden=false;await loadTrace(id,true)}
async function loadTrace(id,scroll){
  try{state.trace=await api(`/v1/tracking/workflows/${encodeURIComponent(id)}/trace`);renderDetail();renderConsole(scroll);setConnection(true)}
  catch(error){setConnection(false);$("console").textContent=`추적 데이터를 불러오지 못했습니다: ${error.message}`}
}
function renderDetail(){
  const envelope=state.trace.session;const run=envelope.resource;const related=state.trace.relatedRuns?.[0]?.session?.resource;const steps=[...(run.steps||[]),...(related?.steps||[])];
  const done=steps.filter(step=>step.status==="completed").length;
  const percent=related?.progress?.percent??run.progress?.percent??(steps.length?Math.round(done/steps.length*100):(run.status==="completed"?100:0));
  $("workflowType").textContent=run.workflowType||"WORKFLOW";$("workflowGoal").textContent=run.goal||"실행 목표 없음";
  $("workflowId").textContent=run.id;$("workflowStatus").textContent=statusName(run.status);$("workflowStatus").className=`status-pill ${run.status}`;
  $("progressValue").textContent=`${percent}%`;$("progressBar").style.width=`${percent}%`;$("revisionValue").textContent=envelope.revision;
  $("createdValue").textContent=time(envelope.createdAt);$("updatedValue").textContent=time(envelope.updatedAt);
  $("stepList").innerHTML=steps.length?steps.map((step,index)=>`<div class="step ${esc(step.status)}"><span class="step-index">${step.status==="completed"?"✓":index+1}</span><div><strong>${esc(step.id)}</strong><p>${esc(step.message||step.operation||step.phase||"작업 단계")}</p></div><span class="step-state">${esc(step.status)}</span></div>`).join(""):`<div class="step ${esc(run.status)}"><span class="step-index">${run.status==="completed"?"✓":"1"}</span><div><strong>${esc(run.goal)}</strong><p>${esc(run.message||"백엔드 작업")}</p></div><span class="step-state">${esc(run.status)}</span></div>`;
}
function messageFor(event){
  const snapshot=event.snapshot||{};const current=snapshot.currentStepId;const progress=snapshot.progress;
  if(progress?.message)return progress.message;
  if(snapshot.message)return snapshot.message;
  if(snapshot.status==="completed")return `완료 · 결과 저장됨${snapshot.result?.frameCount?` · ${snapshot.result.frameCount} frames`:""}`;
  if(snapshot.status==="failed")return `실패 · ${snapshot.lastError?.message||event.reason}`;
  if(current)return `${event.reason} · 다음 단계: ${current}`;
  return event.reason;
}
function renderConsole(scroll){
  const events=[...(state.trace.events||[]),...(state.trace.relatedRuns||[]).flatMap(run=>run.events||[])].sort((a,b)=>new Date(a.createdAt)-new Date(b.createdAt));
  $("consoleMeta").textContent=`${events.length}개 상태 이벤트 · 전체 히스토리`;
  $("console").innerHTML=events.map(event=>{const status=event.snapshot?.status;return `<div class="console-line"><span class="console-time">${time(event.createdAt).split(" ").pop()}</span><span class="console-rev">rev.${event.afterRevision??"-"}</span><span class="console-message ${status==="completed"?"success":status==="failed"?"error":""}">${esc(messageFor(event))}</span></div>`}).join("")||'<div class="console-placeholder">기록된 이벤트가 없습니다.</div>';
  if(scroll&&$("liveToggle").checked)$("console").scrollTop=$("console").scrollHeight;
}

document.querySelectorAll(".filter").forEach(button=>button.onclick=()=>{document.querySelectorAll(".filter").forEach(item=>item.classList.remove("active"));button.classList.add("active");state.status=button.dataset.status;loadRuns()});
$("refreshButton").onclick=loadRuns;$("copyConsole").onclick=async()=>{await navigator.clipboard.writeText($("console").innerText);$("copyConsole").textContent="복사됨";setTimeout(()=>$("copyConsole").textContent="복사",900)};
state.timer=setInterval(()=>{if($("liveToggle").checked)loadRuns()},2000);loadRuns();
