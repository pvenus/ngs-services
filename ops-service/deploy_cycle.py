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

from deploy_control import KEY, bounded_private, is_sha, reject, secure_directory, stamp


TARGETS=(
    ('ngs-local','로컬','ngs'),('ngs-space','스페이스','ngs'),
    ('request-service','Request Service','services'),('rs-mcp','RS-MCP','services'),('workflow-planner','Workflow Planner','services'),
    ('backend-stack','백엔드 스택','services'),
    ('worker-document','문서 워커','workers'),('worker-image','이미지 워커','workers'),
    ('worker-animation','애니메이션 워커','workers'),('worker-postprocess','후처리 워커','workers'),
    ('worker-audio','오디오 워커','workers'),('worker-file','파일 워커','workers'),
)
GROUPS=(('ngs','NGS'),('services','서비스'),('workers','워커'))
IDS={item[0] for item in TARGETS}
READY='ops.deploy-target-ready.v1'
RECEIPT='ops.deploy-target-receipt.v1'
TERMINAL='ops.deploy-target-result.v1'
ACTIVE='ops.deploy-cycle-active.v1'


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


def persist(path,value):
    raw=(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
    fd, tmp=tempfile.mkstemp(prefix='.ops-cycle-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(raw);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


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


def fixed_runner(directory,record):
    immutable=record['runnerSha256']
    path=directory/'run-once'
    check(verified_runner(path)==immutable,'OPS_TARGET_EXECUTABLE_CHANGED')
    env={'HOME':str(Path.home()),'PATH':'/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin',
         'OPS_DEPLOY_CANDIDATE_SHA':record['sha'],'OPS_DEPLOY_ARTIFACT_SHA256':record['artifactSha256']}
    with tempfile.TemporaryFile(dir=directory) as out:
        p=subprocess.run([str(path)],cwd=directory,env=env,stdin=subprocess.DEVNULL,stdout=out,
                         stderr=subprocess.DEVNULL,check=False)
        out.seek(0)
        payload=out.read(8193)
    check(p.returncode==0 and len(payload)<=8192,'OPS_TARGET_RUNNER_FAILED')
    return json.loads(payload)


def proof(value,target,sha):
    native=('deploymentId','providerSha') if target=='ngs-space' else ()
    check(exact(value,'schemaVersion','target','sha','deployedAt','state','health',*native) and
          value['schemaVersion']==RECEIPT and value['target']==target and value['sha']==sha and
          value['state']=='deployed' and value['health']=='verified','OPS_TARGET_RECEIPT_INVALID')
    return observed({key:value[key] for key in ('sha','deployedAt',*native)},target)


class DeploymentCycle:
    def __init__(self,root,*,runner=fixed_runner):
        self.root=Path(root)
        self.runner=runner
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
            check(exact(value,'schemaVersion','selection','targets','startedAt') and value['schemaVersion']==ACTIVE and
                  type(value['selection']) is str and value['selection'] in IDS|{k for k,_ in GROUPS}|{'all'} and
                  type(value['targets']) is list and value['targets'] and
                  len(value['targets'])==len(set(value['targets'])) and set(value['targets']) <= set(self._selection(value['selection'])),
                  'OPS_DEPLOY_JOURNAL_INVALID')
            stamp(value['startedAt'])
        return value

    def _target(self,target):
        path=self.root/'targets'/target
        secure_directory(path)
        initial=observed(optional(path/'observed.json'),target)
        state=optional(path/'last-result.json')
        if state is not None:
            check(exact(state,'schemaVersion','target','attemptedSha','state','finishedAt','observed') and
                  state['schemaVersion']==TERMINAL and state['target']==target and is_sha(state['attemptedSha']) and
                  state['state'] in ('deployed','failed'),'OPS_TARGET_RESULT_INVALID')
            stamp(state['finishedAt'])
            result=observed(state['observed'],target)
            if state['state']=='deployed':
                check(result and result['sha']==state['attemptedSha'],'OPS_TARGET_RESULT_INVALID')
            if result and (initial is None or result['deployedAt']>=initial['deployedAt']):
                initial=result
        record=optional(path/'ready.json')
        if record is not None:
            check(exact(record,'schemaVersion','target','sha','verifiedAt','artifactSha256','runnerSha256') and
                  record['schemaVersion']==READY and record['target']==target and is_sha(record['sha']) and
                  sha256(record['artifactSha256']) and sha256(record['runnerSha256']),'OPS_TARGET_READINESS_INVALID')
            stamp(record['verifiedAt'])
            try: executable=verified_runner(path/'run-once')
            except (OSError,ValueError):executable=None
            if executable!=record['runnerSha256']:
                return (None,initial,'not_configured')
        else:
            # Distinguish a provisioned runner waiting for artifacts from a missing runner.
            try: verified_runner(path/'run-once')
            except (OSError,ValueError):return (None,initial,'not_configured')
            return (None,initial,'pending')
        if state and state['attemptedSha']==record['sha'] and state['state']=='failed':
            return (record,initial,'failed')
        if initial and initial['sha']==record['sha']:
            return (record,initial,'current')
        return (record,initial,'ready')

    def _snapshot(self):
        configured=self._configured()
        locked=False
        try: journal=self._journal() if configured else None
        except (OSError,ValueError,TypeError,OverflowError):
            journal=None;locked=True
        active=({'selection':journal['selection'],'targets':journal['targets']} if journal and self.active==journal else None)
        unknown=bool(journal and active is None)
        rows,plans=[],{}
        for target,label,group in TARGETS:
            try: record,initial,phase=self._target(target) if configured else (None,None,'not_configured')
            except FileNotFoundError:record,initial,phase=None,None,'not_configured'
            except (OSError,ValueError,TypeError,OverflowError):record,initial,phase=None,None,'unknown'
            if journal and target in journal['targets']:
                phase='unknown' if unknown else 'running'
            row={'id':target,'label':label,'group':group,'state':phase,
                 'candidateSha':record['sha'] if record else None,'observed':initial,
                 'ready':phase in ('ready','current') and not locked and not unknown,
                 'canDeploy':phase=='ready' and not (locked or journal)}
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
        return ({'groups':groups,'targets':rows,'allReady':all_ready,'canDeployAll':all_ready,
                 'active':active,'comparison':compare},plans,bool(journal or locked))

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

    def _terminal(self,target,record,status,prior):
        persist(self.root/'targets'/target/'last-result.json',
                {'schemaVersion':TERMINAL,'target':target,'attemptedSha':record['sha'],
                 'state':status,'finishedAt':now(),'observed':prior})

    def _plan_still_exact(self,target,record):
        current,_,phase=self._target(target)
        check(current==record and phase=='ready','OPS_TARGET_PLAN_CHANGED')

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

    def trigger(self,selection):
        if type(selection) is not str:reject(400,'OPS_DEPLOY_SELECTION_INVALID')
        targets=self._selection(selection)
        with self.guard:
            status,plans,blocked=self._snapshot()
            if blocked:
                reject(409,'OPS_DEPLOY_CYCLE_BUSY_OR_UNVERIFIED')
            if not all(key in plans for key in targets):
                reject(503,'OPS_DEPLOY_TARGETS_UNAVAILABLE')
            staged=[(key,plans[key][0],plans[key][1]) for key in targets if plans[key][2]=='ready']
            if not staged:return status
            fd=None
            try:
                fd=self._executor_lock()
                # Another process could have published a journal since selection.
                _,fresh,blocked=self._snapshot()
                if blocked or any(fresh.get(key)!=plans[key] for key in targets):
                    reject(409,'OPS_DEPLOY_CYCLE_PLAN_CHANGED')
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

    def sweep(self):
        """An opt-in periodic host can admit one verified target per tick."""
        status=self.status()
        if status['active'] is not None:return status
        preferred=self._selection('all')
        pending={row['id'] for row in status['targets'] if row['canDeploy']}
        if not pending:return status
        return self.trigger(next(key for key in preferred if key in pending))
