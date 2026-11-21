"""Fixed Mac OPS-only release cycle; no arbitrary targets, args, shell or Git credentials."""
from contextlib import contextmanager
import datetime
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request

REPO='pvenus/ngs-services'
REMOTE='https://github.com/'+REPO+'.git'
WORKFLOW='.github/workflows/prepare-ops-release.yml'
SUPPORT=Path.home()/'Workspaces/NGS/runtime/ops-self-update'
DEPLOY_CONTROL=Path.home()/'Workspaces/NGS/runtime/deploy-control'
RELEASES=Path.home()/'Workspaces/BackendServices/code/releases'
LABEL='com.vinus.npcgen.ops'
PLIST=Path.home()/'Library/LaunchAgents'/(LABEL+'.plist')
DOMAIN='gui/'+str(os.getuid())
PORT=8090
PYTHON='/opt/homebrew/bin/python3.12'
SHA=r'[a-f0-9]{40}'
HEX=r'[a-f0-9]{64}'
ENV={'HOME':str(Path.home()),'PATH':'/opt/homebrew/bin:/usr/bin:/bin','GIT_CONFIG_NOSYSTEM':'1',
     'GIT_CONFIG_GLOBAL':'/dev/null','GIT_TERMINAL_PROMPT':'0','GIT_ALLOW_PROTOCOL':'https'}


def digest(value):return hashlib.sha256(value).hexdigest()
def require(condition,code):
    if not condition:raise RuntimeError(code)


def read(path,limit=128*1024,mode=None):
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as f:
        s=os.fstat(f.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_uid==os.getuid() and s.st_nlink==1 and
                (mode is None or stat.S_IMODE(s.st_mode)==mode) and 0<s.st_size<=limit,'UNTRUSTED_CONTROL_FILE')
        raw=f.read(limit+1)
        require(len(raw)==s.st_size and path.lstat().st_ino==s.st_ino and os.fstat(f.fileno()).st_mtime_ns==s.st_mtime_ns,
                'CONTROL_FILE_CHANGED')
        return raw


def durable(path,raw,mode=0o600):
    fd,tmp=tempfile.mkstemp(prefix='.ops-',dir=path.parent)
    try:
        os.fchmod(fd,mode)
        with os.fdopen(fd,'wb') as out:
            out.write(raw);out.flush();os.fsync(out.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def write_json(path,value):durable(path,(json.dumps(value,sort_keys=True)+'\n').encode())
def command(args,*,cwd=None,timeout=30,check=True):
    return subprocess.run(args,cwd=cwd,env=ENV,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                          check=check,timeout=timeout)


def verify_workflow(rows,head):
    items=rows.get('workflow_runs')
    if not isinstance(items,list):return None
    exact=[r for r in items if isinstance(r,dict) and r.get('head_sha')==head and r.get('event')=='push' and
           r.get('head_branch')=='main' and r.get('path')==WORKFLOW and
           (r.get('repository') or {}).get('full_name')==REPO and
           (r.get('head_repository') or {}).get('full_name')==REPO and
           all(type(r.get(k)) is int and r[k]>0 for k in ('id','run_attempt'))]
    if not exact:return None
    latest=max(exact,key=lambda r:(r['id'],r['run_attempt']))
    return {'runId':str(latest['id']),'runAttempt':str(latest['run_attempt'])} if (
        latest.get('status')=='completed' and latest.get('conclusion')=='success') else None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def github_receipt(head):
    require(re.fullmatch(SHA,head),'CANONICAL_HEAD_INVALID')
    query=urllib.parse.urlencode({'event':'push','branch':'main','head_sha':head,'per_page':20})
    url='https://api.github.com/repos/'+REPO+'/actions/workflows/prepare-ops-release.yml/runs?'+query
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    request=urllib.request.Request(url,headers={'Accept':'application/vnd.github+json','User-Agent':'ngs-ops-local-release',
                                               'X-GitHub-Api-Version':'2022-11-28'})
    with opener.open(request,timeout=10) as r:
        raw=r.read(2*1024*1024+1)
    require(len(raw)<=2*1024*1024,'GITHUB_RECEIPT_TOO_LARGE')
    return verify_workflow(json.loads(raw),head)


def prepare(work, installed):
    git=['/usr/bin/git','-C',str(work)]
    command([*git,'init','--quiet','--template='])
    command([*git,'fetch','--depth=128','--no-tags',REMOTE,'refs/heads/main'],timeout=50)
    head=command([*git,'rev-parse','--verify','FETCH_HEAD^{commit}']).stdout.decode().strip()
    if head==installed:return ({'state':'current'},None)
    require(re.fullmatch(SHA,head) and re.fullmatch(SHA,installed) and
        command([*git,'merge-base','--is-ancestor',installed,head],check=False).returncode==0,
        'OPS_BASELINE_NOT_IN_BOUNDED_CANONICAL_HISTORY')
    proof=github_receipt(head)
    if not proof:return ({'state':'pending'},None)
    tree=command([*git,'rev-parse','--verify',head+':ops-service']).stdout.decode().strip()
    require(re.fullmatch(SHA,tree),'OPS_TREE_INVALID')
    data=command([*git,'archive','--format=tar',head,'ops-service'],timeout=12).stdout
    require(0<len(data)<=16*1024*1024,'OPS_ARCHIVE_INVALID')
    return {'sha':head,'tree':tree,'archiveSha256':digest(data),'proof':proof},data


def materialize(candidate,directory,data):
    import io
    require(digest(data)==candidate['archiveSha256'],'OPS_ARTIFACT_DIGEST_CHANGED')
    allowed=[]
    with tarfile.open(fileobj=io.BytesIO(data),mode='r:') as arc:
        members=arc.getmembers()
        require(len(members)<=1000,'OPS_ARCHIVE_ENTRY_LIMIT')
        for item in members:
            parts=item.name.split('/')
            require(parts[0]=='ops-service' and all(re.fullmatch(r'[A-Za-z0-9._-]+',p) and p not in ('.','..') for p in parts)
                    and (item.isdir() or item.isfile()) and item.size<=2*1024*1024,'OPS_ARCHIVE_UNSAFE_MEMBER')
            allowed.append(item.name)
        require(len(allowed)==len(set(allowed)) and 'ops-service/server.py' in allowed,'OPS_ARTIFACT_SHAPE_INVALID')
        arc.extractall(directory,filter='data')
    require((directory/'ops-service/server.py').is_file(),'OPS_ENTRY_MISSING')


def source_path(sha):return RELEASES/('ops-only-github-'+sha)


def render(previous,old_directory,new_directory):
    old=Path(old_directory)
    value=plistlib.loads(previous)
    require(value.get('Label')==LABEL and value.get('ProgramArguments')==[PYTHON,'-B','-u',str(old/'server.py')] and
            value.get('WorkingDirectory')==str(old),'OPS_LAUNCH_BASELINE_DRIFT')
    value['ProgramArguments'][-1]=str(new_directory/'server.py')
    value['WorkingDirectory']=str(new_directory)
    return plistlib.dumps(value,sort_keys=False)


def launch(*args,check=True):
    return command(['/bin/launchctl',*args],check=check,timeout=15)


def live(path):
    out=launch('print',DOMAIN+'/'+LABEL,check=False)
    match=re.search(rb'^\s*pid = (\d+)$',out.stdout,re.M)
    return int(match[1]) if (not out.returncode and match and b'state = running' in out.stdout and os.fsencode(path) in out.stdout) else None


def port_listening():
    result=command(['/usr/sbin/lsof','-nP','-iTCP:'+str(PORT),'-sTCP:LISTEN','-Fp'],check=False)
    return result.stdout.strip()


def alive(pid):
    if not pid:return False
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False


def await_(condition,seconds,code):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        try:
            result=condition()
            if result:return result
        except (OSError,http.client.HTTPException,ValueError):pass
        time.sleep(.25)
    raise RuntimeError(code)


def listener_pid(fields):
    pids=set()
    for row in fields.splitlines():
        if row.startswith(b'f'):  # lsof appends file-descriptor records even for -Fp.
            if not re.fullmatch(rb'f[0-9]+',row):return None
            continue
        if not re.fullmatch(rb'p[1-9][0-9]*',row):return None
        pids.add(int(row[1:]))
    return next(iter(pids)) if len(pids)==1 else None


def health(path, *, new_ui):
    pid=live(path)
    if not pid or listener_pid(port_listening())!=pid:return None
    for route in ('/requests','/services','/alarms','/api/official-requests?limit=1','/api/service-status','/api/alarms')+(
        ('/deployments',) if new_ui else ()):
        conn=http.client.HTTPConnection('127.0.0.1',PORT,timeout=3)
        try:
            conn.request('GET',route);r=conn.getresponse()
            if r.status!=200 or not r.read(1024):return None
        finally:conn.close()
    return pid


def stop(pid):
    launch('bootout',DOMAIN+'/'+LABEL)
    await_(lambda:not alive(pid) and not port_listening() and launch('print',DOMAIN+'/'+LABEL,check=False).returncode,12,
           'OPS_DRAIN_NOT_VERIFIED')
    time.sleep(.6)


def start(ready,new_ui):
    launch('bootstrap',DOMAIN,str(PLIST))
    return await_(lambda:health(ready,new_ui=new_ui),35,'OPS_HEALTH_NOT_VERIFIED')


def state(value):
    require(isinstance(value,dict) and set(value)=={'sha','tree','plistSha256','directory'} and
        all(type(value[k]) is str for k in value) and re.fullmatch(SHA,value['sha']) and re.fullmatch(SHA,value['tree']) and
        re.fullmatch(HEX,value['plistSha256']),'INVALID_OPS_INSTALLED_RECORD')
    directory=Path(value['directory'])
    previous=RELEASES/('ngs-services-github-'+value['sha'][:7])/'ops-service'
    current=source_path(value['sha'])/'ops-service'
    require(directory in (previous,current) and directory.resolve()==directory and directory.is_dir(),'UNTRUSTED_OPS_RELEASE_ROOT')
    return value


def read_json(path):
    return json.loads(read(path,8192,0o600))


def transaction_receipt(value):
    require(isinstance(value,dict) and set(value)=={'old','new','mode','phase','provenance'} and
        type(value['mode']) is int and value['mode'] in (0o600,0o644) and
        value['phase'] in ('switching','deployed','rolled_back'),'INVALID_OPS_JOURNAL')
    state(value['old']);state(value['new'])
    return value


def settle_transaction(loc,txn,prefer_previous=False):
    old,selected=txn['old'],txn['new']
    previous=read(loc/'prior.plist',mode=0o600)
    chosen=read(loc/'candidate.plist',mode=0o600)
    require(digest(previous)==old['plistSha256'] and digest(chosen)==selected['plistSha256'] and
        render(previous,old['directory'],Path(selected['directory']))==chosen,'OPS_RECOVERY_BYTES_DRIFT')
    current=read(PLIST)
    require(current in (previous,chosen),'OPS_EXTERNAL_PLIST_CHANGE')
    installed=state(read_json(loc/'installed.json'))
    require(installed in (old,selected),'OPS_EXTERNAL_STATE_CHANGE')
    old_entry=Path(old['directory'])/'server.py'
    new_entry=Path(selected['directory'])/'server.py'
    if not prefer_previous and current==chosen and health(new_entry,new_ui=True):
        write_json(loc/'installed.json',selected)
        txn['phase']='deployed'
    else:
        running=launch('print',DOMAIN+'/'+LABEL,check=False)
        if not running.returncode:
            if current==previous and health(old_entry,new_ui=False):
                write_json(loc/'installed.json',old)
                txn['phase']='rolled_back'
                write_json(loc/'transaction.json',txn)
                return result('rolled_back',old['sha'])
            pid=live(new_entry) if current==chosen else live(old_entry)
            require(pid,'UNRECOGNIZED_OPS_PROCESS')
            stop(pid)
        else:
            require(not port_listening(),'OPS_PORT_OWNED_OUTSIDE_APPROVED_LABEL')
        durable(PLIST,previous,txn['mode'])
        start(old_entry,False)
        write_json(loc/'installed.json',old)
        txn['phase']='rolled_back'
    write_json(loc/'transaction.json',txn)
    return result(txn['phase'],old['sha'] if txn['phase']=='rolled_back' else selected['sha'])


def result(status,sha=None):
    return {'schemaVersion':'ops.self-update-status.v1','target':'ops','state':status,'sha':sha,
            'checkedAt':datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}


@contextmanager
def cycle_fence(control=None):
    """Avoid replacing an OPS process while it owns another target's apply."""
    control=DEPLOY_CONTROL if control is None else control
    try:
        parent=control.lstat()
    except FileNotFoundError:
        yield True
        return
    require(control.resolve()==control and stat.S_ISDIR(parent.st_mode) and
            parent.st_uid==os.getuid() and stat.S_IMODE(parent.st_mode)==0o700,
            'OPS_DEPLOY_CONTROL_UNTRUSTED')
    lock=control/'cycle.lock'
    fd=os.open(lock,os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_RDWR,0o600)
    try:
        st=os.fstat(fd)
        require(stat.S_ISREG(st.st_mode) and st.st_uid==os.getuid() and st.st_nlink==1 and
                stat.S_IMODE(st.st_mode)==0o600 and lock.lstat().st_ino==st.st_ino,
                'OPS_DEPLOY_CONTROL_LOCK_UNTRUSTED')
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        os.close(fd)


def apply_cycle(root):
    try:
        prior_attempt=transaction_receipt(read_json(root/'transaction.json'))
    except FileNotFoundError:
        prior_attempt=None
    if prior_attempt and prior_attempt['phase']=='switching':
        # A crashed cycle does not launch a second deployment before reconciliation.
        with cycle_fence() as acquired:
            if not acquired:return result('busy',prior_attempt['old']['sha'])
            return settle_transaction(root,prior_attempt)
    previous_state=state(read_json(root/'installed.json'))
    previous=read(PLIST)
    mode=stat.S_IMODE(PLIST.lstat().st_mode)
    require(digest(previous)==previous_state['plistSha256'] and mode in (0o600,0o644),'OPS_BASELINE_CHANGED')
    old_dir=Path(previous_state['directory'])
    pid=health(old_dir/'server.py',new_ui=False)
    require(pid,'PRIOR_OPS_NOT_HEALTHY')
    with tempfile.TemporaryDirectory(prefix='cycle-',dir=root) as temp:
        candidate,data=prepare(Path(temp),previous_state['sha'])
        if data is None:return result(candidate['state'],previous_state['sha'])
        if candidate['tree']==previous_state['tree']:return result('unchanged',previous_state['sha'])
        if prior_attempt and prior_attempt['phase']=='rolled_back' and prior_attempt['new']['sha']==candidate['sha']:
            return result('previous_attempt_rolled_back',previous_state['sha'])
        dst=source_path(candidate['sha'])
        # Always verify an already extracted release, not blindly reuse its files.
        with tempfile.TemporaryDirectory(prefix='.ops-verify-',dir=RELEASES) as work:
            target=Path(work)
            materialize(candidate,target,data)
            if dst.exists():
                require(dst.resolve()==dst and dst.is_dir() and dst.stat().st_uid==os.getuid(),'UNTRUSTED_CANDIDATE_DIRECTORY')
                expected={q.relative_to(target):digest(q.read_bytes()) for q in target.rglob('*') if q.is_file()}
                actual={q.relative_to(dst):digest(read(q,2*1024*1024)) for q in dst.rglob('*') if q.is_file() or q.is_symlink()}
                require(actual==expected,'CANDIDATE_RELEASE_DRIFT')
            else:
                os.rename(target,dst)
        selected=render(previous,old_dir,dst/'ops-service')
        selected_state={'sha':candidate['sha'],'tree':candidate['tree'],'plistSha256':digest(selected),'directory':str(dst/'ops-service')}
        # Recheck the exact canonical head just before launchd mutation.
        remote=command(['/usr/bin/git','ls-remote','--exit-code',REMOTE,'refs/heads/main'],timeout=15).stdout
        require(remote==os.fsencode(candidate['sha']+'\trefs/heads/main\n'),'CANONICAL_HEAD_MOVED')
        with cycle_fence() as acquired:
            if not acquired:return result('busy',previous_state['sha'])
            require(read(PLIST)==previous and health(old_dir/'server.py',new_ui=False)==pid,'OPS_BASELINE_MOVED')
            durable(root/'prior.plist',previous);durable(root/'candidate.plist',selected)
            txn={'old':previous_state,'new':selected_state,'mode':mode,'phase':'switching','provenance':candidate}
            write_json(root/'transaction.json',txn)
            try:
                stop(pid);durable(PLIST,selected,mode)
                start(dst/'ops-service/server.py',True)
                require(read(PLIST)==selected,'OPS_POST_APPLY_BYTES_DRIFT')
                return settle_transaction(root,txn)
            except Exception:
                return settle_transaction(root,txn,prefer_previous=True)


def cycle(root=SUPPORT):
    st=root.lstat()
    require(root.resolve()==root and stat.S_ISDIR(st.st_mode) and st.st_uid==os.getuid() and
            stat.S_IMODE(st.st_mode)==0o700,'OPS_SELF_UPDATE_NOT_PROVISIONED')
    lock=os.open(root/'cycle.lock',os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_RDWR,0o600)
    with os.fdopen(lock,'rb') as fd:
        ls=os.fstat(fd.fileno())
        require(stat.S_ISREG(ls.st_mode) and ls.st_uid==os.getuid() and stat.S_IMODE(ls.st_mode)==0o600 and
            ls.st_nlink==1 and (root/'cycle.lock').lstat().st_ino==ls.st_ino,'OPS_LOCK_UNTRUSTED')
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return result('busy')
        try:out=apply_cycle(root)
        except Exception as error:
            msg=str(error)
            safe=msg if type(error) is RuntimeError and re.fullmatch(r'[A-Z][A-Z_0-9]{3,63}',msg) else 'OPS_SELF_UPDATE_FAILED'
            out={**result('failed'),'reason':safe}
        write_json(root/'status.json',out)
        return out


if __name__=='__main__':
    # launchd invokes this detached no-argument job only for OPS itself.
    if len(sys.argv)!=1:raise SystemExit(64)
    try:out=cycle()
    except Exception:out=result('failed')
    print(json.dumps(out,separators=(',',':')))
    raise SystemExit(1 if out['state'] in ('failed','rolled_back','previous_attempt_rolled_back') else 0)
