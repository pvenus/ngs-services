"""OPS deployment catalog and common fixed target cycle, independent of HTTP."""
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import threading
import uuid

from deploy_control import KEY, bounded_private, is_sha, reject, secure_directory, stamp
from release_equivalence import actual_selector_sha, equivalent_release
from deploy.prepare_ops_candidates_once import image_retry_baseline
from deploy.prepare_ops_candidates_once import current_worker_idle


TARGETS=(
    ('ngs-local','로컬','ngs'),('ngs-space','스페이스','ngs'),
    ('request-service','Request Service','services'),('rs-mcp','RS-MCP','services'),('workflow-planner','Workflow Planner','services'),
    ('filex-writer','Filex 저장기','services'),('backend-stack','백엔드 스택','services'),
    ('worker-document','문서 워커','workers'),('worker-image','이미지 워커','workers'),
    ('worker-animation','애니메이션 워커','workers'),('worker-postprocess','후처리 워커','workers'),
    ('worker-audio','오디오 워커','workers'),('worker-file','파일 워커','workers'),
)
GROUPS=(('ngs','NGS'),('services','서비스'),('workers','워커'))
IDS={item[0] for item in TARGETS}
WORKER_IDS={t for t,_,group in TARGETS if group=='workers'}
WORKER_DECISION='ops.worker-source-decision.v1'
READY='ops.deploy-target-ready.v1'
IMAGE_RETRY='worker-image'
IMAGE_RETRY_READY='ops.worker-manual-retry-ready.v1'
IMAGE_RETRY_ARCHIVE='ops.worker-manual-retry-admission.v1'
RECEIPT='ops.deploy-target-receipt.v1'
ERROR='ops.deploy-target-error.v1'
PHASES={'preflight','unload','selector','start','health'}
ROLLBACK={'not_attempted','restored','failed'}
ERROR_CODES=frozenset((
  'TARGET_INPUT_INVALID TARGET_UNSUPPORTED TARGET_CANDIDATE_MISMATCH '
  'TARGET_RELEASE_TREE_MISMATCH TARGET_SELECTOR_CHANGED TARGET_PRIOR_NOT_RUNNING '
  'TARGET_SELECTOR_LABEL TARGET_SELECTOR_ARGUMENTS TARGET_SELECTOR_ENTRY TARGET_RS_MCP_SELECTOR '
  'TARGET_SELECTOR_WORKDIR TARGET_UNHANDLED_RELEASE_REFERENCE TARGET_UNHANDLED_RELEASE_ARG '
  'TARGET_RELEASE_ENTRY_MISSING TARGET_AUDIO_ADAPTER TARGET_POSTPROCESS_ADAPTER '
  'TARGET_LAUNCH_STATUS_FAILED TARGET_UNLOAD_FAILED TARGET_UNLOAD_TIMEOUT TARGET_BOOTSTRAP_FAILED '
  'TARGET_START_FAILED TARGET_HEALTH_TIMEOUT TARGET_HEALTH_HTTP TARGET_OPERATION_TIMEOUT '
  'TARGET_PRIVATE_INPUT_INVALID TARGET_APPLY_REFUSED'
).split())
# The fixed Local adapter is standalone and carries the same finite protocol.
NGS_LOCAL_PHASES = frozenset((
    "validate_candidate", "check_current", "stop_current", "wait_ports", "switch_selector",
    "start_candidate", "check_candidate", "check_storage", "preflight_audio_schema", "record_result", "unknown",
))
NGS_LOCAL_ROLLBACK_PHASES = frozenset(("rollback_stop", "rollback_restore", "rollback_audio_schema", "rollback_start", "rollback_check"))
NGS_LOCAL_ERRORS = frozenset((
    'ARCHIVE_BLOB_DRIFT',
    'ARCHIVE_PATH_INVALID',
    'ARCHIVE_TREE_DRIFT',
    'GITHUB_WORKFLOW_NOT_VERIFIED',
    'INVALID_LOCAL_DEPLOY_RECEIPT',
    'INVALID_RELEASE_SHA',
    'LOCAL_DEPLOY_PLAN_DRIFT',
    'LOCAL_DEPLOY_RUNNER_UNAVAILABLE',
    'MAC_BOOTOUT_FAILED',
    'MAC_BOOTSTRAP_FAILED',
    'MAC_CANDIDATE_VALIDATION_FAILED',
    'MAC_CURRENT_CHECK_FAILED',
    'MAC_DEPLOY_LOCK_BUSY',
    'MAC_PORT_CHECK_FAILED',
    'MAC_RESULT_WRITE_FAILED',
    'MAC_RETRY_ID_INVALID',
    'MAC_RETRY_HISTORY_INVALID',
    'MAC_RETRY_ATTEMPT_EXISTS',
    'MAC_RETRY_PARENT_DRIFT',
    'MAC_RETRY_ROLLBACK_REQUIRED',
    'MAC_RETRY_PRIOR_DRIFT',
    'MAC_ROLLBACK_CHECK_FAILED',
    'MAC_ROLLBACK_RESTORE_FAILED',
    'MAC_SELECTOR_SWITCH_FAILED',
    'MAC_STORAGE_CHECK_FAILED',
    'MAC_RELEASE_INTERRUPTED',
    'MAC_RELEASE_REFUSED',
    'MINIFLARE_DEPENDENCIES_NOT_LOCKED',
    'MINIFLARE_DEPENDENCIES_NOT_READY',
    'MINIFLARE_LOCK_INVALID',
    'NATIVE_AUTH_SCOPE_DRIFT',
    'NATIVE_AUTH_UNAVAILABLE',
    'NATIVE_HEALTH_UNVERIFIED',
    'NATIVE_MCP_UNAVAILABLE',
    'NATIVE_PLIST_DRIFT',
    'NATIVE_PORT_OWNER_DRIFT',
    'NATIVE_PROCESS_PIN_DRIFT',
    'NATIVE_QUEUE_UNAVAILABLE',
    'NATIVE_RAW_ROUTE_UNAVAILABLE',
    'NATIVE_RELEASE_SHA_DRIFT',
    'NATIVE_ROLE_DRIFT',
    'NATIVE_SSD_DRIFT',
    'NATIVE_SSD_UNAVAILABLE',
    'NATIVE_STATE_DRIFT',
    'NATIVE_STORAGE_IDENTITY_DRIFT',
    'NATIVE_AUDIO_SCHEMA_UNPROVEN',
    'NATIVE_AUDIO_STORAGE_NOT_STOPPED',
    'NATIVE_AUDIO_SEPARATION_ATTESTATION_INVALID',
    'NGS_LOCAL_CAUSE_NOT_RECORDED',
    'OLD_NATIVE_PORTS_BUSY',
    'OPS_PROVENANCE_INVALID',
    'OPS_RELEASE_NOT_PREPARED',
    'PRIOR_RELEASE_DRIFT',
    'PRIVATE_RELEASE_REQUIRED',
    'RELEASE_ALREADY_ATTEMPTED_RECONCILE',
    'RELEASE_ARCHIVE_DRIFT',
    'RELEASE_DIGEST_INVALID',
    'RELEASE_LINK_DRIFT',
    'RELEASE_PROVENANCE_INVALID',
    'RELEASE_TREE_DRIFT',
    'RELEASE_TREE_MODE_DRIFT',
    'RELEASE_TREE_OWNER_DRIFT',
    'ROLLBACK_SELECTOR_DRIFT',
    'ROLLBACK_STORAGE_IDENTITY_DRIFT',
    'STORAGE_STARTUP_INPUTS_CHANGED',
))
NGS_LOCAL_UNRECORDED = "NGS_LOCAL_CAUSE_NOT_RECORDED"
TERMINAL='ops.deploy-target-result.v1'
SPACE_PUBLICATION_SCHEMA='ops.ngs-space-publication.v1'
SPACE_SITE='appgprj_6abcee8b33688191bed4fec75f26b91f'
SPACE_HOST='vinus-npcgen.park-venus0518.chatgpt.site'
ACTIVE='ops.deploy-cycle-active.v1'
PREPARATION='ops.candidate-preparation-status.v1'
PREPARATION_IDS={id for id,_,group in TARGETS if group in ('workers','services')} | {'filex-writer'}
PREPARATION_STATES={'ready','current','blocked','pending','auth_required','failed',
                    'in_progress','equivalent','not_configured'}
SUPERVISED_SERVICE_PREP={'request-service','workflow-planner'}
PREPARATION_CODE=re.compile(r'[A-Z][A-Z0-9_]{1,79}')

RETRY_RUNNER='ops.ngs-local-retry-runner.v1'
RETRY_ARCHIVE='ops.ngs-local-retry-admission.v1'


def retry_id(value, *, previous=False):
    return type(value) is str and ((previous and value == 'initial') or
           (re.fullmatch(r'[0-9a-f]{32}', value) is not None and value != '0'*32))


def unique_pairs(pairs):
    value=dict(pairs)
    check(len(value)==len(pairs),'OPS_NGS_RETRY_PROOF_INVALID')
    return value


def retry_read(path, limit=4096):
    return json.loads(bounded_private(path,limit),object_pairs_hook=unique_pairs)


def latest_local_attempt(folder):
    directory=folder/'attempts'
    if not os.path.lexists(directory):
        return 'initial',folder
    secure_directory(directory)
    index=retry_read(directory/'current.json',32768)
    check(exact(index,'attemptIds') and type(index['attemptIds']) is list,'OPS_NGS_RETRY_PROOF_INVALID')
    ids=index['attemptIds']
    check(0<len(ids)<=512 and all(retry_id(i) for i in ids) and len(set(ids))==len(ids),
          'OPS_NGS_RETRY_PROOF_INVALID')
    with os.scandir(directory) as entries:
        names=set()
        for entry in entries:
            check(len(names)<=len(ids),'OPS_NGS_RETRY_PROOF_INVALID')
            names.add(entry.name)
    check(names=={'current.json',*ids},'OPS_NGS_RETRY_PROOF_INVALID')
    for item in ids:secure_directory(directory/item)
    return ids[-1],directory/ids[-1]


def preparation_status(root):
    try:
        value=retry_read(root/'ops-candidate-preparation-status.json',16384)
        check(exact(value,'schemaVersion','checkedAt','targets') and value['schemaVersion']==PREPARATION
              and type(value['targets']) is dict and set(value['targets']) <= PREPARATION_IDS,
              'OPS_PREPARATION_STATUS_INVALID')
        stamp(value['checkedAt'])
        for item in value['targets'].values():
            check(type(item) is dict and set(item) in ({'state'},{'state','sha'},
                  {'state','code'},{'state','sha','code'}) and type(item['state']) is str
                  and item['state'] in PREPARATION_STATES and ('sha' not in item or is_sha(item['sha']))
                  and ('code' not in item or type(item['code']) is str and PREPARATION_CODE.fullmatch(item['code'])),
                  'OPS_PREPARATION_STATUS_INVALID')
        return {'checkedAt':value['checkedAt'],'targets':value['targets']}
    except (OSError,ValueError,KeyError,AttributeError,TypeError,OverflowError,RecursionError):
        return None


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def exact(value,*keys):
    return type(value) is dict and set(value)==set(keys)


def check(condition,code):
    if not condition:raise ValueError(code)


def sha256(value):
    return type(value) is str and bool(KEY.fullmatch(value))


def optional(path,size=4096):
    try:return json.loads(bounded_private(path,size))
    except FileNotFoundError:return None


def space_publication(path):
    item=optional(path/'publication.json',1024)
    if item is None:return None
    check(item=={'schemaVersion':SPACE_PUBLICATION_SCHEMA,'provider':'chatgpt-sites',
          'projectId':SPACE_SITE,'hostname':SPACE_HOST,'accessMode':'custom'},
          'OPS_SPACE_PUBLICATION_INVALID')
    return item


def space_handoff(path):
    item=optional(path/'handoff.json',1024)
    if (not exact(item,'threadId','title') or type(item['threadId']) is not str or
            not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}',item['threadId']) or
            item['title']!='비나리 스페이스 배포'):
        return None
    return item


def persist(path,value):
    raw=(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
    fd, tmp=tempfile.mkstemp(prefix='.ops-cycle-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(raw);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def sync_dir(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def observed(value,target):
    if value is None:return None
    native=('deploymentId','providerSha') if target=='ngs-space' else ()
    check(exact(value,'sha','deployedAt',*native) and is_sha(value['sha']),'OPS_OBSERVATION_INVALID')
    stamp(value['deployedAt'])
    if native:
        check(is_sha(value['providerSha']) and type(value['deploymentId']) is str and bool(
            re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}',value['deploymentId'])),'OPS_NATIVE_OBSERVATION_INVALID')
    return value


def verified_runner(path):
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as f:
        attrs=os.fstat(f.fileno())
        check(stat.S_ISREG(attrs.st_mode) and attrs.st_uid==os.getuid() and attrs.st_nlink==1 and
              stat.S_IMODE(attrs.st_mode) in (0o500,0o700) and 0<attrs.st_size<=1024*1024,'OPS_TARGET_EXECUTABLE_INVALID')
        data=f.read(1024*1024+1)
        post=os.fstat(f.fileno())
        check(len(data)==attrs.st_size and post.st_mtime_ns==attrs.st_mtime_ns and
              path.lstat().st_ino==attrs.st_ino,'OPS_TARGET_EXECUTABLE_CHANGED')
    return hashlib.sha256(data).hexdigest()


class TargetFailure(Exception):
    def __init__(self, diagnostic):
        self.diagnostic=diagnostic


def safe_failure(value, target=None):
    if target == 'ngs-local':
        check((exact(value,'phase','code','rollback') or exact(value,'phase','code','rollback','rollbackFailure'))
              and all(type(value[k]) is str for k in ('phase','code','rollback'))
              and value['phase'] in NGS_LOCAL_PHASES and value['code'] in NGS_LOCAL_ERRORS
              and value['rollback'] in ROLLBACK | {'unknown'}, 'OPS_TARGET_ERROR_INVALID')
        if 'rollbackFailure' in value:
            detail=value['rollbackFailure']
            check(value['rollback']=='failed' and exact(detail,'stage','code') and
                  type(detail['stage']) is str and type(detail['code']) is str and
                  detail['stage'] in NGS_LOCAL_ROLLBACK_PHASES and detail['code'] in NGS_LOCAL_ERRORS,
                  'OPS_TARGET_ERROR_INVALID')
        return value
    check(exact(value,'phase','code','rollback') and
          all(type(value[k]) is str for k in value) and value['phase'] in PHASES and
          value['code'] in ERROR_CODES and value['rollback'] in ROLLBACK,'OPS_TARGET_ERROR_INVALID')
    return value


def local_legacy_failure(control, attempted_sha):
    """Read the old phase-less, exact-SHA durable result; never repair/retry."""
    try:
        releases=control/'mac-releases'
        secure_directory(releases)
        folder=releases/attempted_sha
        secure_directory(folder)
        result=optional(folder/'result.json')
    except (OSError,ValueError,TypeError,OverflowError,RecursionError):
        return None
    if result in ({'state':'rolled-back'},{'state':'rollback-failed'}):
        return {'phase':'unknown','code':NGS_LOCAL_UNRECORDED,
                'rollback':'restored' if result['state']=='rolled-back' else 'failed'}
    return None


def fixed_runner(directory,record,*,retry=None):
    immutable=record['runnerSha256']
    path=directory/'run-once'
    check(verified_runner(path)==immutable,'OPS_TARGET_EXECUTABLE_CHANGED')
    env={'HOME':str(Path.home()),'PATH':'/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin',
         'OPS_DEPLOY_CANDIDATE_SHA':record['sha'],'OPS_DEPLOY_ARTIFACT_SHA256':record['artifactSha256']}
    if retry is not None:
        check(directory.name=='ngs-local','OPS_NGS_RETRY_PROOF_INVALID')
        env.update(OPS_DEPLOY_RETRY_ATTEMPT_ID=retry['attemptId'],
                   OPS_DEPLOY_RETRY_PREVIOUS_ATTEMPT_ID=retry['previousAttemptId'],
                   OPS_DEPLOY_RETRY_NATIVE_SHA256=retry['nativeSha256'])
    with tempfile.TemporaryFile(dir=directory) as out:
        p=subprocess.run([str(path)],cwd=directory,env=env,stdin=subprocess.DEVNULL,stdout=out,
                         stderr=subprocess.DEVNULL,check=False)
        out.seek(0)
        payload=out.read(8193)
    check(len(payload)<=8192,'OPS_TARGET_RUNNER_FAILED')
    value=json.loads(payload)
    if p.returncode:
        fields=('schemaVersion','target','sha','phase','code','rollback')
        diagnostics=('phase','code','rollback')
        if retry is not None:fields+=('attemptId',)
        if directory.name=='ngs-local' and type(value) is dict and 'rollbackFailure' in value:
            fields+=('rollbackFailure',)
            diagnostics+=('rollbackFailure',)
        check(exact(value,*fields) and value['schemaVersion']==ERROR and value['target']==directory.name and
              value['sha']==record['sha'] and (retry is None or value['attemptId']==retry['attemptId']),
              'OPS_TARGET_RUNNER_FAILED')
        raise TargetFailure(safe_failure({k:value[k] for k in diagnostics},directory.name))
    if retry is not None:
        check(type(value) is dict and value.get('attemptId')==retry['attemptId'],'OPS_TARGET_RECEIPT_INVALID')
        value={k:v for k,v in value.items() if k!='attemptId'}
    return value


def proof(value,target,sha):
    native=('deploymentId','providerSha') if target=='ngs-space' else ()
    check(exact(value,'schemaVersion','target','sha','deployedAt','state','health',*native) and
          value['schemaVersion']==RECEIPT and value['target']==target and value['sha']==sha and
          value['state']=='deployed' and value['health']=='verified','OPS_TARGET_RECEIPT_INVALID')
    return observed({key:value[key] for key in ('sha','deployedAt',*native)},target)


class DeploymentCycle:
    def __init__(self,root,*,runner=fixed_runner,retry_probe=image_retry_baseline,worker_probe=current_worker_idle,home=None):
        self.root=Path(root)
        self.runner=runner
        self.retry_probe=retry_probe
        self.worker_probe=worker_probe
        self.home=Path.home() if home is None else Path(home)
        self.guard=threading.RLock()
        self.active=None

    def _configured(self):
        try:
            secure_directory(self.root)
            secure_directory(self.root/'targets')
            return True
        except (OSError,ValueError):
            return False

    def _selection(self,selection):
        if selection=='all':
            # Keep backends ahead of their workers, and local ahead of Space.
            order=('services','workers','ngs')
            return [key for group in order for key,_,existing in TARGETS if existing==group]
        if selection in {row[0] for row in GROUPS}:
            return [key for key,_,group in TARGETS if group==selection]
        if selection in IDS:return [selection]
        reject(400,'OPS_DEPLOY_SELECTION_INVALID')

    def _journal(self):
        value=optional(self.root/'active.json')
        if value is not None:
            retry='retry' in value if type(value) is dict else False
            worker_retry='workerRetry' in value if type(value) is dict else False
            extra=['retry'] if retry else ['workerRetry'] if worker_retry else []
            check(not (retry and worker_retry) and exact(value,'schemaVersion','selection','targets','startedAt',*extra) and value['schemaVersion']==ACTIVE and
                  type(value['selection']) is str and value['selection'] in IDS|{k for k,_ in GROUPS}|{'all'} and
                  type(value['targets']) is list and value['targets'] and
                  len(value['targets'])==len(set(value['targets'])) and set(value['targets']) <= set(self._selection(value['selection'])),
                  'OPS_DEPLOY_JOURNAL_INVALID')
            stamp(value['startedAt'])
            if retry:
                item=value['retry']
                check(value['selection']=='ngs-local' and value['targets']==['ngs-local'] and
                      exact(item,'sha','attemptId','previousAttemptId') and is_sha(item['sha']) and
                      retry_id(item['attemptId']) and retry_id(item['previousAttemptId'],previous=True) and
                      item['attemptId']!=item['previousAttemptId'],'OPS_DEPLOY_JOURNAL_INVALID')
            if worker_retry:
                item=value['workerRetry']
                check(value['selection']==IMAGE_RETRY and value['targets']==[IMAGE_RETRY] and
                      exact(item,'sha','attemptId','offerSha256') and is_sha(item['sha']) and
                      retry_id(item['attemptId']) and sha256(item['offerSha256']),'OPS_DEPLOY_JOURNAL_INVALID')
        return value

    def _service_preparation(self,target,record,initial,phase):
        if target not in SUPERVISED_SERVICE_PREP:
            return phase
        status=preparation_status(self.root)
        item=status['targets'].get(target) if status else None
        if item is None:
            if phase=='ready' and (record.get('manualOnly') is True or
                    status is None and os.path.lexists(self.root/'ops-candidate-preparation-status.json')):
                return 'pending'  # Do not resurrect a supervised candidate without its status.
            return phase  # Previously trusted, explicitly registered manual plan.
        requested=item.get('sha')
        if requested and (not initial or initial['sha']!=requested) and item['state'] in ('blocked','failed'):
            return 'blocked'
        if phase=='ready' and (item!={'state':'ready','sha':record['sha']} or
              not 0<=(stamp(now())-stamp(status['checkedAt'])).total_seconds()<=900):
            return 'pending'
        return phase

    def _target(self,target):
        path=self.root/'targets'/target
        secure_directory(path)
        initial=observed(optional(path/'observed.json'),target)
        state=optional(path/'last-result.json')
        if state is not None:
            fields=('schemaVersion','target','attemptedSha','state','finishedAt','observed')
            if type(state) is dict and 'attemptId' in state:
                check(target in ('ngs-local',IMAGE_RETRY) and retry_id(state['attemptId']),'OPS_TARGET_RESULT_INVALID')
                fields+=('attemptId',)
            check((exact(state,*fields) or exact(state,*fields,'failure')) and
                  state['schemaVersion']==TERMINAL and state['target']==target and is_sha(state['attemptedSha']) and
                  state['state'] in ('deployed','failed'),'OPS_TARGET_RESULT_INVALID')
            if 'failure' in state:
                check(state['state']=='failed','OPS_TARGET_RESULT_INVALID')
                safe_failure(state['failure'],target)
            stamp(state['finishedAt'])
            result=observed(state['observed'],target)
            if state['state']=='deployed':
                check(result and result['sha']==state['attemptedSha'],'OPS_TARGET_RESULT_INVALID')
            if result and (initial is None or result['deployedAt']>=initial['deployedAt']):
                initial=result
        if target=='ngs-space' and space_publication(path) is None:
            return (None,initial,'not_configured')
        record=optional(path/'ready.json')
        if record is not None:
            fields=('schemaVersion','target','sha','verifiedAt','artifactSha256','runnerSha256')
            check((exact(record,*fields) or
                   (exact(record,*fields,'manualOnly') and record['manualOnly'] is True)) and
                  record['schemaVersion']==READY and record['target']==target and is_sha(record['sha']) and
                  sha256(record['artifactSha256']) and sha256(record['runnerSha256']),'OPS_TARGET_READINESS_INVALID')
            stamp(record['verifiedAt'])
            if target=='ngs-local' and state and state['attemptedSha']==record['sha'] and state['state']=='failed':
                # Historical failed candidates stay failed (not actionable) even after adapter repinning.
                return (record,initial,'failed')
            if (target=='ngs-local' and state and 'attemptId' in state and state['state']=='deployed' and
                    state['attemptedSha']==record['sha'] and initial and initial['sha']==record['sha']):
                # The immutable historical ready may still contain the earlier
                # ordinary adapter pin. The retry's separately verified result
                # can be displayed as current, but never as an executable plan.
                return (record,initial,'current')
            try: executable=verified_runner(path/'run-once')
            except (OSError,ValueError):executable=None
            if executable!=record['runnerSha256']:
                return (None,initial,'not_configured')
        else:
            # Distinguish a provisioned runner waiting for artifacts from a missing runner.
            try: verified_runner(path/'run-once')
            except (OSError,ValueError):return (None,initial,'not_configured')
            return (None,initial,self._service_preparation(target,None,initial,'pending'))
        failed=state and state['attemptedSha']==record['sha'] and state['state']=='failed'
        if target in WORKER_IDS:
            # Prepared worker deployments must prove a real per-target delta,
            # not merely an old board-level candidate/ready record.
            if initial and initial['sha']==record['sha'] and not failed:
                return (record,initial,'current')
            try:decision=self._worker_decision(path,target,record,initial)
            except (OSError,ValueError,KeyError,TypeError):
                if failed and target==IMAGE_RETRY:
                    return (record,initial,'failed')
                raise
            if decision=='equivalent':
                check(equivalent_release(path,target,record,initial,
                      selector_sha=lambda key:actual_selector_sha(key,home=self.home)),
                      'OPS_TARGET_SOURCE_DECISION_INVALID')
                return (record,initial,'current')
            if failed:
                return (record,initial,'failed')
            if decision=='changed':
                return (record,initial,'ready')
            # A legacy proof can only suppress execution on transitional
            # installs; no old ready marker may enable any worker deployment.
            if initial and equivalent_release(path,target,record,initial,
                      selector_sha=lambda key:actual_selector_sha(key,home=self.home)):
                return (record,initial,'current')
            return (record,initial,'pending')
        if failed:
            return (record,initial,'failed')
        phase='current' if initial and (initial['sha']==record['sha'] or
                                       equivalent_release(path,target,record,initial)) else 'ready'
        return (record,initial,self._service_preparation(target,record,initial,phase))

    def _worker_decision(self,path,target,record,initial):
        file=path/'worker-source-decision.json'
        try:
            decision=retry_read(file)
        except FileNotFoundError:
            return None
        fields=('schemaVersion','target','sha','artifactSha256','runnerSha256','selectorSha256',
                'observedSha','activeTreeSha256','candidateTreeSha256','state')
        check(type(decision) is dict and decision.get('state') in ('equivalent','changed') and
              exact(decision,*fields,*(['manifestSha256'] if decision.get('state')=='changed' else [])),
              'OPS_TARGET_SOURCE_DECISION_INVALID')
        # Interrupted/moved publication is not an executable old plan.
        if decision['sha']!=record['sha']:
            return None
        check(decision['schemaVersion']==WORKER_DECISION and decision['target']==target and
              record.get('manualOnly') is True and initial and decision['observedSha']==initial['sha'] and
              decision['artifactSha256']==record['artifactSha256'] and decision['runnerSha256']==record['runnerSha256'] and
              sha256(decision['activeTreeSha256']) and sha256(decision['candidateTreeSha256']) and
              decision['selectorSha256']==actual_selector_sha(target,home=self.home) and
              (decision['activeTreeSha256']==decision['candidateTreeSha256'])==(decision['state']=='equivalent'),
              'OPS_TARGET_SOURCE_DECISION_INVALID')
        if decision['state']=='changed':
            raw=bounded_private(path/'candidate.json',4096)
            candidate=json.loads(raw,object_pairs_hook=unique_pairs)
            check(decision['manifestSha256']==hashlib.sha256(raw).hexdigest() and
                  candidate['target']==target and candidate['sha']==record['sha'] and
                  candidate['archiveSha256']==record['artifactSha256'] and
                  candidate['expectedPriorPlistSha256']==decision['selectorSha256'],'OPS_TARGET_SOURCE_DECISION_INVALID')
            status=preparation_status(self.root)
            if (not status or status['targets'].get(target)!={'state':'ready','sha':record['sha']}
                    or not 0<=(stamp(now())-stamp(status['checkedAt'])).total_seconds()<=900):
                return None
        return decision['state']

    def _retry_plan(self,record,initial):
        # Results/pins are just eligibility evidence. The native runner repeats
        # the selector, health, prepared archive and parent checks under its lock.
        try:
            check(record is not None and initial is not None and initial['sha']!=record['sha'],
                  'OPS_NGS_RETRY_PROOF_INVALID')
            target=self.root/'targets/ngs-local'
            prior=retry_read(target/'last-result.json')
            check(prior['attemptedSha']==record['sha'] and prior['state']=='failed','OPS_NGS_RETRY_PROOF_INVALID')
            config=retry_read(target/'retry-runner.json')
            check(exact(config,'schemaVersion','adapterSha256','nativeSha256') and config['schemaVersion']==RETRY_RUNNER
                  and sha256(config['adapterSha256']) and sha256(config['nativeSha256']),
                  'OPS_NGS_RETRY_PROOF_INVALID')
            secure_directory(self.root/'bin')
            check(verified_runner(target/'run-once')==config['adapterSha256'] and
                  verified_runner(self.root/'bin/ngs-mac-apply')==config['nativeSha256'],'OPS_NGS_RETRY_PROOF_INVALID')
            prepared=retry_read(self.root/'prepared.json')
            sha=record['sha']
            check(prepared['schemaVersion']=='ops.ngs-prepared-deploy.v1' and prepared['sha']==sha and
                  prepared['mac']=={'ready':True} and
                  prepared['provenance']['archiveSha256']==record['artifactSha256'],'OPS_NGS_RETRY_PROOF_INVALID')
            releases=self.root/'mac-releases'
            secure_directory(releases)
            folder=releases/sha
            secure_directory(folder)
            latest,attempt=latest_local_attempt(folder)
            check(prior.get('attemptId','initial')==latest,'OPS_NGS_RETRY_PROOF_INVALID')
            result=retry_read(attempt/'result.json')
            check(exact(result,'state') or exact(result,'state','failure'),'OPS_NGS_RETRY_PROOF_INVALID')
            if 'failure' in result:
                item=result['failure']
                check(exact(item,'stage','code') and type(item['stage']) is str and type(item['code']) is str and
                      item['stage'] in NGS_LOCAL_PHASES-{'unknown'} and item['code'] in NGS_LOCAL_ERRORS,
                      'OPS_NGS_RETRY_PROOF_INVALID')
            check(result['state']=='rolled-back' and (latest=='initial' or 'failure' in result),'OPS_NGS_RETRY_PROOF_INVALID')
            manifest=retry_read(folder/'manifest.json')
            prior_sha=hashlib.sha256(bounded_private(attempt/'prior.plist',65536)).hexdigest()
            check(manifest['schemaVersion']=='ops.ngs-mac-release.v1' and manifest['sha']==sha and
                  manifest['archiveSha256']==record['artifactSha256'] and
                  manifest['expectedPriorPlistSha256']==prior_sha,'OPS_NGS_RETRY_PROOF_INVALID')
            return ({**record,'runnerSha256':config['adapterSha256']},initial,
                    {'previousAttemptId':latest,'nativeSha256':config['nativeSha256']},prior)
        except (OSError,ValueError,KeyError,AttributeError,TypeError,OverflowError,RecursionError):
            return None

    def _snapshot(self):
        configured=self._configured()
        locked=False
        try: journal=self._journal() if configured else None
        except (OSError,ValueError,TypeError,OverflowError):
            journal=None;locked=True
        active=({'selection':journal['selection'],'targets':journal['targets']} if journal and self.active==journal else None)
        preparation=preparation_status(self.root) if configured else None
        unknown=bool(journal and active is None)
        rows,plans=[],{}
        for target,label,group in TARGETS:
            try: record,initial,phase=self._target(target) if configured else (None,None,'not_configured')
            except FileNotFoundError:record,initial,phase=None,None,'not_configured'
            except (OSError,ValueError,TypeError,OverflowError):record,initial,phase=None,None,'unknown'
            if journal and target in journal['targets']:
                phase='unknown' if unknown else 'running'
            prepared=preparation['targets'].get(target) if preparation and target in SUPERVISED_SERVICE_PREP else None
            candidate_sha=prepared.get('sha') if phase=='blocked' and prepared else record['sha'] if record else None
            row={'id':target,'label':label,'group':group,'state':phase,
                 'candidateSha':candidate_sha,'observed':initial,
                 'ready':phase in ('ready','current') and not locked and not unknown,
                 'canDeploy':phase=='ready' and not (locked or journal)}
            if record is not None and record.get('manualOnly') is True:
                row['manualOnly']=True
            if target=='ngs-space' and configured:
                try: publication=space_publication(self.root/'targets'/target)
                except (OSError,ValueError): publication=None
                if publication:
                    row['publication']={'provider':publication['provider'],'hostname':publication['hostname']}
                    try: handoff=space_handoff(self.root/'targets'/target)
                    except (OSError,ValueError): handoff=None
                    if handoff:row['publication']['handoff']=handoff
            if phase=='failed' and record is not None:
                last=optional(self.root/'targets'/target/'last-result.json')
                if last and last.get('attemptedSha')==record['sha'] and 'failure' in last:
                    row['failure']=safe_failure(last['failure'],target)
                elif target=='ngs-local' and last and last.get('attemptedSha')==record['sha'] and 'attemptId' not in last:
                    legacy=local_legacy_failure(self.root,record['sha'])
                    if legacy is not None:row['failure']=legacy
            if target=='ngs-local' and phase=='failed' and not (locked or journal):
                retry=self._retry_plan(record,initial)
                if retry:
                    row['retry']={'sha':record['sha'],'previousAttemptId':retry[2]['previousAttemptId']}
            if target==IMAGE_RETRY and phase=='failed' and not (locked or journal):
                retry=self._image_retry_plan(record['sha'])
                if retry:
                    row['workerRetry']={'sha':record['sha'],'offerSha256':retry[0]}
                    row['canDeploy']=True
                    row['manualOnly']=True
            if row['ready']:plans[target]=(record,initial,phase)
            rows.append(row)
        ready={row['id']:row['ready'] for row in rows}
        groups=[
          {'id':id_,'label':label,'targets':self._selection(id_),
           'canDeploy':not (locked or journal) and all(ready[key] for key in self._selection(id_))}
          for id_,label in GROUPS
        ]
        local,space=rows[:2]
        compare=None
        if not locked and local['observed'] and space['observed'] and all(
            row['state'] not in ('running','failed','unknown') for row in (local,space)):
            compare='match' if local['observed']['sha']==space['observed']['sha'] else 'mismatch'
        all_ready=bool(not (locked or journal) and all(ready.values()))
        # Space publication and Local same-version retries have independent
        # approval paths; bulk cutover covers only actionable ordinary rows.
        can_deploy_all=any(r['id']!='ngs-space' and r['canDeploy'] and r['state']=='ready' for r in rows)
        return ({'groups':groups,'targets':rows,'allReady':all_ready,'canDeployAll':can_deploy_all,
                 'active':active,'comparison':compare,**({'preparation':preparation} if preparation else {})},
                plans,bool(journal or locked))

    def status(self):
        with self.guard:return self._snapshot()[0]

    def _executor_lock(self):
        fd=os.open(self.root/'cycle.lock',os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_RDWR,0o600)
        try:
            attrs=os.fstat(fd)
            check(stat.S_ISREG(attrs.st_mode) and attrs.st_uid==os.getuid() and attrs.st_nlink==1 and
                 stat.S_IMODE(attrs.st_mode)==0o600 and (self.root/'cycle.lock').lstat().st_ino==attrs.st_ino,
                 'OPS_DEPLOY_LOCK_INVALID')
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            return fd
        except BaseException:
            os.close(fd);raise

    def _terminal(self,target,record,status,prior,diagnostic=None,*,attempt_id=None):
        persist(self.root/'targets'/target/'last-result.json',
                {'schemaVersion':TERMINAL,'target':target,'attemptedSha':record['sha'],
                 'state':status,'finishedAt':now(),'observed':prior,
                 **({'failure':diagnostic} if diagnostic else {}),
                 **({'attemptId':attempt_id} if attempt_id else {})})

    def _plan_still_exact(self,target,record):
        current,_,phase=self._target(target)
        check(current==record and phase=='ready','OPS_TARGET_PLAN_CHANGED')
        if target in WORKER_IDS:
            pin=actual_selector_sha(target,home=self.home)
            check(self.worker_probe(self.home,target,pin) is True,'OPS_WORKER_LIVE_BASELINE_UNPROVEN')

    def _run(self,fd,job,staged):
        safely_done=False
        try:
            # Revalidate the whole admission plan before the first mutating adapter.
            for target,record,_ in staged:self._plan_still_exact(target,record)
        except (OSError,ValueError,TypeError):
            # No executable started; release the journal for a later valid plan.
            safely_done=True
        else:
            try:
                for target,record,previous in staged:
                    try:self._plan_still_exact(target,record)
                    except (OSError,ValueError,TypeError):
                        break
                    directory=self.root/'targets'/target
                    try: after=proof(self.runner(directory,record),target,record['sha'])
                    except TargetFailure as error:
                        self._terminal(target,record,'failed',previous,error.diagnostic)
                        break
                    except Exception:
                        self._terminal(target,record,'failed',previous)
                        break
                    self._terminal(target,record,'deployed',after)
                safely_done=True
            except OSError:
                # The adapter outcome was not durably persisted. Leave the
                # pending journal for explicit reconciliation rather than retry.
                pass
        finally:
            with self.guard:
                try:
                    if safely_done and self._journal()==job:(self.root/'active.json').unlink()
                finally:
                    self.active=None
                    os.close(fd)

    def trigger(self,selection,*,automatic=False):
        if type(selection) is not str:reject(400,'OPS_DEPLOY_SELECTION_INVALID')
        targets=self._selection(selection)
        with self.guard:
            status,plans,blocked=self._snapshot()
            if blocked:
                reject(409,'OPS_DEPLOY_CYCLE_BUSY_OR_UNVERIFIED')
            if selection=='all':
                targets=[key for key in targets if key!='ngs-space' and key in plans]
            elif not all(key in plans for key in targets):
                reject(503,'OPS_DEPLOY_TARGETS_UNAVAILABLE')
            staged=[(key,plans[key][0],plans[key][1]) for key in targets
                    if plans[key][2]=='ready' and not (automatic and plans[key][0].get('manualOnly') is True)]
            if not staged:return status
            expected=[row[0] for row in staged] if selection=='all' else targets
            fd=None
            try:
                fd=self._executor_lock()
                # Another process could have published a journal since selection.
                _,fresh,blocked=self._snapshot()
                if blocked or any(fresh.get(key)!=plans[key] for key in expected):
                    reject(409,'OPS_DEPLOY_CYCLE_PLAN_CHANGED')
                if selection in WORKER_IDS:
                    pin=actual_selector_sha(selection,home=self.home)
                    if self.worker_probe(self.home,selection,pin) is not True:
                        reject(409,'OPS_WORKER_LIVE_BASELINE_UNPROVEN')
                job={'schemaVersion':ACTIVE,'selection':selection,'targets':[row[0] for row in staged],'startedAt':now()}
                persist(self.root/'active.json',job)
                self.active=job
                worker=threading.Thread(target=self._run,args=(fd,job,staged),daemon=True)
                worker.start()
                fd=None # Worker owns the OS lock until all receipts are durable.
                return self._snapshot()[0]
            except BlockingIOError:
                reject(409,'OPS_DEPLOY_CYCLE_BUSY_OR_UNVERIFIED')
            except (OSError,ValueError):
                reject(503,'OPS_DEPLOY_CYCLE_UNAVAILABLE')
            finally:
                if fd is not None:os.close(fd)

    def _manual_plan(self,sha,previous):
        record,initial,phase=self._target('ngs-local')
        if phase!='failed' or record['sha']!=sha:return None
        plan=self._retry_plan(record,initial)
        return plan if plan and plan[2]['previousAttemptId']==previous else None

    def _run_retry(self,fd,job,plan):
        retry=job['retry']
        attempt=retry['attemptId']
        safely_done=False
        try:
            if self._manual_plan(retry['sha'],retry['previousAttemptId'])!=plan:
                # Leave the admitted request visible for explicit reconciliation.
                return
            record,prior,options,_=plan
            try:
                after=proof(self.runner(self.root/'targets/ngs-local',record,
                                        retry={**options,'attemptId':attempt}),'ngs-local',record['sha'])
            except TargetFailure as error:
                self._terminal('ngs-local',record,'failed',prior,error.diagnostic,attempt_id=attempt)
            except Exception:
                self._terminal('ngs-local',record,'failed',prior,attempt_id=attempt)
            else:
                self._terminal('ngs-local',record,'deployed',after,attempt_id=attempt)
            sync_dir(self.root/'targets/ngs-local')
            safely_done=True
        except (OSError,ValueError,TypeError,OverflowError):
            pass
        finally:
            with self.guard:
                try:
                    if safely_done and self._journal()==job:(self.root/'active.json').unlink()
                finally:
                    self.active=None
                    os.close(fd)

    def retry_local(self,sha,previous):
        if not is_sha(sha) or not retry_id(previous,previous=True):
            reject(400,'OPS_NGS_RETRY_SELECTION_INVALID')
        with self.guard:
            _,_,blocked=self._snapshot()
            if blocked:reject(409,'OPS_DEPLOY_CYCLE_BUSY_OR_UNVERIFIED')
            if not self._configured():reject(503,'OPS_DEPLOY_CYCLE_UNAVAILABLE')
            fd=None
            try:
                fd=self._executor_lock()
                if self._journal() is not None:reject(409,'OPS_DEPLOY_CYCLE_BUSY_OR_UNVERIFIED')
                plan=self._manual_plan(sha,previous)
                if not plan:reject(409,'OPS_NGS_RETRY_STALE_OR_UNVERIFIED')
                attempt=uuid.uuid4().hex
                retry={'sha':sha,'attemptId':attempt,'previousAttemptId':previous}
                job={'schemaVersion':ACTIVE,'selection':'ngs-local','targets':['ngs-local'],'startedAt':now(),'retry':retry}
                archive=self.root/'targets/ngs-local/manual-retries'
                archive.mkdir(mode=0o700,exist_ok=True)
                secure_directory(archive)
                destination=archive/(attempt+'.json')
                check(not os.path.lexists(destination),'OPS_NGS_RETRY_PROOF_INVALID')
                # Before handing off execution, pin the previous OPS failure.
                # Native first and per-attempt receipts also remain unchanged.
                persist(destination,{'schemaVersion':RETRY_ARCHIVE,**retry,'acceptedAt':job['startedAt'],
                                     'previousResult':plan[3]})
                sync_dir(archive)
                sync_dir(archive.parent)
                persist(self.root/'active.json',job)
                sync_dir(self.root)
                self.active=job
                worker=threading.Thread(target=self._run_retry,args=(fd,job,plan),daemon=True)
                try:worker.start()
                except RuntimeError:
                    self.active=None
                    reject(503,'OPS_DEPLOY_CYCLE_UNAVAILABLE')
                fd=None
                return {**self._snapshot()[0],'retry':retry}
            except BlockingIOError:
                reject(409,'OPS_DEPLOY_CYCLE_BUSY_OR_UNVERIFIED')
            except (OSError,ValueError,TypeError,OverflowError):
                reject(503,'OPS_DEPLOY_CYCLE_UNAVAILABLE')
            finally:
                if fd is not None:os.close(fd)

    def _image_retry_plan(self,sha,*,live=False):
        target=IMAGE_RETRY
        directory=self.root/'targets'/target
        try:
            record,initial,phase=self._target(target)
            check(phase=='failed' and record is not None and record['sha']==sha and
                  record.get('manualOnly') is True and initial and initial['sha']!=sha,'OPS_WORKER_RETRY_UNVERIFIED')
            prep=preparation_status(self.root)
            check(prep and prep['targets'].get(target)=={'state':'ready','sha':sha}
                  and 0<=(stamp(now())-stamp(prep['checkedAt'])).total_seconds()<=900,
                  'OPS_WORKER_RETRY_UNVERIFIED')
            raw=bounded_private(directory/'retry-ready.json',4096)
            offer=json.loads(raw,object_pairs_hook=unique_pairs)
            check(exact(offer,'schemaVersion','target','sha','failureSha256','candidateSha256','runnerSha256')
                  and offer['schemaVersion']==IMAGE_RETRY_READY and offer['target']==target and offer['sha']==sha
                  and offer['runnerSha256']==record['runnerSha256'],'OPS_WORKER_RETRY_UNVERIFIED')
            last_raw=bounded_private(directory/'last-result.json',4096)
            manifest_raw=bounded_private(directory/'candidate.json',4096)
            check(offer['failureSha256']==hashlib.sha256(last_raw).hexdigest() and
                  offer['candidateSha256']==hashlib.sha256(manifest_raw).hexdigest(),'OPS_WORKER_RETRY_UNVERIFIED')
            last=json.loads(last_raw,object_pairs_hook=unique_pairs)
            manifest=json.loads(manifest_raw,object_pairs_hook=unique_pairs)
            check('failure' in last and last['observed']==initial and exact(manifest,'schemaVersion','target','sha','archiveSha256',
                  'treeSha256','expectedPriorPlistSha256') and manifest['schemaVersion']=='ops.mac-target-candidate.v1'
                  and manifest['target']==target and manifest['sha']==sha
                  and manifest['archiveSha256']==record['artifactSha256'] and sha256(manifest['treeSha256'])
                  and manifest['expectedPriorPlistSha256']==actual_selector_sha(target,home=self.home),
                  'OPS_WORKER_RETRY_UNVERIFIED')
            if live:
                check(self.retry_probe(self.home,target,manifest['expectedPriorPlistSha256'],
                                       initial['sha'],last['finishedAt']) is True,'OPS_WORKER_RETRY_UNVERIFIED')
            return (hashlib.sha256(raw).hexdigest(),record,initial,last_raw)
        except (OSError,ValueError,KeyError,AttributeError,TypeError,OverflowError,RecursionError):
            return None

    def _run_image_retry(self,fd,job,expected):
        target=IMAGE_RETRY
        retry=job['workerRetry']
        settled=False
        try:
            if self._image_retry_plan(retry['sha'],live=True)!=expected:
                settled=True # Refused without invoking the executable.
                return
            _,record,prior,_=expected
            try:
                value=self.runner(self.root/'targets'/target,record)
                after=proof(value,target,record['sha'])
            except TargetFailure as error:
                self._terminal(target,record,'failed',prior,error.diagnostic,attempt_id=retry['attemptId'])
            else:
                self._terminal(target,record,'deployed',after,attempt_id=retry['attemptId'])
            sync_dir(self.root/'targets'/target)
            settled=True
        except Exception:
            # Missing trustworthy execution receipt: retain the exact journal.
            pass
        finally:
            with self.guard:
                try:
                    if settled and self._journal()==job:
                        (self.root/'active.json').unlink()
                        sync_dir(self.root)
                finally:
                    self.active=None
                    os.close(fd)

    def retry_worker(self,target,sha,offer_sha):
        if target!=IMAGE_RETRY or not is_sha(sha) or not sha256(offer_sha):
            reject(400,'OPS_WORKER_RETRY_SELECTION_INVALID')
        with self.guard:
            fd=None
            try:
                if not self._configured():reject(503,'OPS_DEPLOY_CYCLE_UNAVAILABLE')
                fd=self._executor_lock()
                if self._journal() is not None:reject(409,'OPS_DEPLOY_CYCLE_BUSY_OR_UNVERIFIED')
                plan=self._image_retry_plan(sha,live=True)
                if not plan or plan[0]!=offer_sha:
                    reject(409,'OPS_WORKER_RETRY_STALE_OR_UNVERIFIED')
                attempt=uuid.uuid4().hex
                payload={'sha':sha,'attemptId':attempt,'offerSha256':offer_sha}
                job={'schemaVersion':ACTIVE,'selection':target,'targets':[target],'startedAt':now(),'workerRetry':payload}
                archive=self.root/'targets'/target/'manual-retries'
                archive.mkdir(mode=0o700,exist_ok=True)
                secure_directory(archive)
                previous=archive/(attempt+'.previous.json')
                admission=archive/(attempt+'.json')
                check(not os.path.lexists(previous) and not os.path.lexists(admission),'OPS_WORKER_RETRY_UNVERIFIED')
                with os.fdopen(os.open(previous,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'wb') as out:
                    out.write(plan[3]);out.flush();os.fsync(out.fileno())
                persist(admission,{'schemaVersion':IMAGE_RETRY_ARCHIVE,'target':target,**payload,
                                   'acceptedAt':job['startedAt'],'previousResultSha256':hashlib.sha256(plan[3]).hexdigest()})
                sync_dir(archive)
                sync_dir(archive.parent)
                persist(self.root/'active.json',job)
                sync_dir(self.root)
                self.active=job
                try:
                    threading.Thread(target=self._run_image_retry,args=(fd,job,plan),daemon=True).start()
                except RuntimeError:
                    self.active=None
                    reject(503,'OPS_DEPLOY_CYCLE_UNAVAILABLE')
                fd=None
                return {**self._snapshot()[0],'workerRetry':payload}
            except BlockingIOError:
                reject(409,'OPS_DEPLOY_CYCLE_BUSY_OR_UNVERIFIED')
            except (OSError,ValueError,TypeError,OverflowError):
                reject(503,'OPS_DEPLOY_CYCLE_UNAVAILABLE')
            finally:
                if fd is not None:os.close(fd)

    def sweep(self):
        """An opt-in periodic host can admit one verified target per tick."""
        status=self.status()
        if status['active'] is not None:return status
        preferred=self._selection('all')
        pending={row['id'] for row in status['targets']
                 if row['canDeploy'] and row['state']=='ready' and row.get('manualOnly') is not True}
        if not pending:return status
        return self.trigger(next(key for key in preferred if key in pending),automatic=True)
