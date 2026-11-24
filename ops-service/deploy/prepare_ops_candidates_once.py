"""Private Mac OPS candidate detector. Never applies, activates or publishes."""
from contextlib import closing, contextmanager
from datetime import UTC, datetime
import http.client
import fcntl
import hashlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import pwd
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace


STATUS = 'ops-candidate-preparation-status.json'
SCHEMA = 'ops.candidate-preparation-status.v1'
LOCK = 'prepare-ops-candidates.lock'
PIN_NAME = 'ops-candidate-preparer-helpers.json'
PIN_SCHEMA = 'ops.candidate-preparer-helpers.v1'
HELPERS = (
    'prepare_ngs_release', 'github_actions_keychain', 'prepare_ngs_local_once',
    'planner_cutover', 'audio_cutover', 'mac_target_apply', 'prepare_rs_relay',
    'prepare_service_targets', 'github_target_candidate_evidence',
    'prepare_worker_candidates', 'prepare_service_candidates',
    'prepare_filex_writer', 'prepare_filex_supervised',
)
# Only the private checksum manifest can admit these two mutually dependent
# helpers; never fall through to names on a Python installation search path.
FILEX_HELPERS = ('filex_writer_apply','filex_writer_active')
FILEX_UNIFIED_HELPER = 'filex_unified_runtime'

SERVICES = 'pvenus/ngs-services'
WORKERS = 'pvenus/ngs-workers'
SERVICE_TARGETS = ('request-service', 'workflow-planner', 'backend-stack')
WORKER_TARGETS = tuple('worker-' + key for key in ('document','image','animation','postprocess','audio','file'))
RELAY = 'rs-mcp'
FILEX = 'filex-writer'
TARGETS = (*SERVICE_TARGETS, *WORKER_TARGETS, RELAY, FILEX)
SHA = re.compile(r'[0-9a-f]{40}')
DIGEST = re.compile(r'[0-9a-f]{64}')
AUTH = frozenset(('GITHUB_KEYCHAIN_UNAVAILABLE GITHUB_KEYCHAIN_ITEM_MISSING GITHUB_KEYCHAIN_LOCKED_OR_DENIED '
                  'GITHUB_KEYCHAIN_ITEM_INVALID GITHUB_TARGET_KEYCHAIN_ITEM_INVALID GITHUB_ACTIONS_READ_DENIED').split())
PENDING = frozenset(('GITHUB_TARGET_RUN_NOT_READY GITHUB_TARGET_RUN_MOVED GITHUB_TARGET_MAIN_MOVED '
                     'GITHUB_ARTIFACT_EXPIRED GITHUB_TARGET_ARTIFACT_AMBIGUOUS MAIN_ADVANCED '
                     'GITHUB_PUBLIC_MAIN_UNAVAILABLE '
                     'WORKER_MAIN_ADVANCED PREP_LOCK_BUSY PREP_ACTIVE_DEPLOYMENT WORKER_DEPLOY_ACTIVE_OR_UNVERIFIED '
                     'SERVICE_LOCAL_ARTIFACT_UNAVAILABLE FILEX_WORKFLOW_NOT_READY FILEX_MAIN_ADVANCED '
                     'FILEX_PREPARATION_LOCK_BUSY').split())
BLOCKED = frozenset(('WORKER_ACTIVE_PROOF_UNAVAILABLE WORKER_ACTIVE_TREE_DRIFT WORKER_LOADED_UNPROVEN WORKER_RETRY_BASELINE_UNPROVEN WORKER_RUNNER_UNVERIFIED WORKER_RUNNER_DEPENDENCY_UNVERIFIED WORKER_SELECTOR_INVALID '
                     'WORKER_RUNTIME_PATH_INVALID WORKER_RUNTIME_DEPENDENCY_UNVERIFIED WORKER_DATABASE_UNVERIFIED '
                     'WORKER_IMAGE_CONFIG_UNVERIFIED WORKER_OUTCOME_UNVERIFIED WORKER_ATTEMPT_FAILED '
                     'WORKER_IDLE_UNPROVEN WORKER_AUDIO_PROOF_DRIFT WORKER_ADMISSION_INPUT_DRIFT '
                     'WORKER_REGISTERED_CANDIDATE_DRIFT WORKER_CANDIDATE_UNVERIFIED SERVICE_PREP_REFUSED '
                     'SERVICE_RECONCILE_REQUIRED SERVICE_PRIOR_SELECTOR_NOT_LOADED SERVICE_SELECTOR_CHANGED '
                     'SERVICE_RUNNER_CHANGED SERVICE_RUNTIME_NOT_PINNED SERVICE_RELEASE_SOURCE_DRIFT '
                     'REQUEST_SELECTOR_STORAGE_DRIFT TARGET_PLANNER_DATABASE_IDENTITY '
                     'BACKEND_STARTUP_OR_STORAGE_ABI_CHANGED '
                     'BACKEND_STORAGE_UNVERIFIED FILEX_COMPANION_NOT_ACTIVE GITHUB_TARGET_EVIDENCE_INVALID '
                     'GITHUB_TARGET_RUN_SOURCE_DENIED GITHUB_TARGET_ARTIFACT_UNVERIFIED GIT_OBJECTS_UNVERIFIED '
                     'HELPER_BUNDLE_UNVERIFIED FILEX_PREPARER_NOT_PROVISIONED FILEX_PREP_CONFIG_INVALID '
                     'FILEX_PRIOR_RUNTIME_UNVERIFIED FILEX_BUILD_PROOF_INVALID FILEX_PREPARATION_CHANGED '
                     'FILEX_RUNNER_PIN_MISMATCH FILEX_RECONCILE_REQUIRED FILEX_ARTIFACT_DIGEST_MISMATCH '
                     'FILEX_ARTIFACT_SCOPE_INVALID FILEX_GIT_PROOF_MISMATCH FILEX_BASE_NOT_PINNED '
                     'FILEX_EXISTING_CANDIDATE_MISMATCH FILEX_OFFLINE_BASE_UNAVAILABLE FILEX_SOURCE_CAPABILITIES_INVALID '
                     'FILEX_ISOLATED_DOCKER_UNAVAILABLE FILEX_ISOLATED_DOCKER_FAILED FILEX_IMAGE_ID_INVALID '
                     'GITHUB_PUBLIC_MAIN_INVALID GITHUB_PUBLIC_MAIN_REPO_DENIED '
                     'FILEX_RUNTIME_HELPERS_UNPROVISIONED FILEX_PREPARATION_UNAVAILABLE PREP_FAILED').split())
STATES = frozenset(('ready current blocked pending auth_required failed in_progress equivalent not_configured').split())
GIT_CONFIG = {
    'core.hooksPath':'/dev/null', 'protocol.allow':'never', 'protocol.https.allow':'always',
    'credential.helper':'', 'http.followRedirects':'false', 'fetch.fsckObjects':'true',
    'fetch.writeCommitGraph':'false', 'maintenance.auto':'false', 'gc.auto':'0',
    'fetch.recurseSubmodules':'false',
}


class Refusal(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise Refusal(code)


def valid(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def private_dir(path):
    s = path.lstat()
    require(path.is_absolute() and path.resolve(strict=True) == path and stat.S_ISDIR(s.st_mode)
            and s.st_uid == os.getuid() and stat.S_IMODE(s.st_mode) == 0o700, 'HELPER_BUNDLE_UNVERIFIED')


def same_inode(a, b):
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def regular(fd, path, mode=0o600):
    a, b = os.fstat(fd), path.lstat()
    require(same_inode(a, b) and a.st_uid == os.getuid() and a.st_nlink == 1
            and stat.S_ISREG(a.st_mode) and stat.S_IMODE(a.st_mode) == mode, 'HELPER_BUNDLE_UNVERIFIED')
    return a


def read_private(path, limit=1024*1024):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as f:
        a = regular(f.fileno(), path)
        require(0 < a.st_size <= limit, 'HELPER_BUNDLE_UNVERIFIED')
        raw = f.read(limit+1)
        b = regular(f.fileno(), path)
        require(len(raw) == a.st_size and (a.st_ctime_ns, a.st_mtime_ns) == (b.st_ctime_ns, b.st_mtime_ns),
                'HELPER_BUNDLE_UNVERIFIED')
        return raw


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def unique(pairs):
    out = {}
    for key, value in pairs:
        require(key not in out, 'HELPER_BUNDLE_UNVERIFIED')
        out[key] = value
    return out


class PinnedHelpers(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, parent):
        private_dir(parent)
        manifest = json.loads(read_private(parent/PIN_NAME, 8192), object_pairs_hook=unique)
        require(type(manifest) is dict and set(manifest)=={'schemaVersion','files'} and
                manifest['schemaVersion']==PIN_SCHEMA and type(manifest['files']) is dict,
                'HELPER_BUNDLE_UNVERIFIED')
        fixed = {name+'.py' for name in HELPERS}
        extras = {name+'.py' for name in FILEX_HELPERS}
        unified={FILEX_UNIFIED_HELPER+'.py'}
        require(set(manifest['files']) in (fixed,fixed|extras,fixed|extras|unified), 'HELPER_BUNDLE_UNVERIFIED')
        self.by_name = {}
        for name in (*HELPERS,*(FILEX_HELPERS if extras <= manifest['files'].keys() else ()),
                     *((FILEX_UNIFIED_HELPER,) if unified <= manifest['files'].keys() else ())):
            path = parent/(name+'.py')
            expected = manifest['files'][path.name]
            raw = read_private(path)
            require(valid(DIGEST, expected) and sha(raw)==expected,
                    'HELPER_BUNDLE_UNVERIFIED')
            self.by_name[name] = (path, raw)

    def find_spec(self, fullname, path=None, target=None):
        if fullname in self.by_name:
            return importlib.util.spec_from_file_location(fullname, self.by_name[fullname][0], loader=self)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        path, raw = self.by_name[module.__name__]
        exec(compile(raw, str(path), 'exec'), module.__dict__)


@contextmanager
def private_job_lock(control):
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(control/LOCK, flags, 0o600)
    try:
        regular(fd, control/LOCK)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        regular(fd, control/LOCK)
        yield True
    finally:
        os.close(fd)


def install_status(control, statuses, stamp):
    state_path = control/STATUS
    if os.path.lexists(state_path):
        with os.fdopen(os.open(state_path, os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as current:
            regular(current.fileno(),state_path)
    document = {'schemaVersion': SCHEMA, 'checkedAt': stamp, 'targets': statuses}
    data = (json.dumps(document, sort_keys=True, separators=(',',':'))+'\n').encode()
    fd, name = tempfile.mkstemp(prefix='.candidate-status-', dir=control)
    try:
        with os.fdopen(fd, 'wb') as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, state_path)
    finally:
        if os.path.lexists(name):
            os.unlink(name)
    return document


def git_values():
    out = {'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null',
           'GIT_TERMINAL_PROMPT':'0','GIT_NO_REPLACE_OBJECTS':'1',
           'GIT_CONFIG_COUNT':str(len(GIT_CONFIG))}
    for i, (key, value) in enumerate(GIT_CONFIG.items()):
        out['GIT_CONFIG_KEY_'+str(i)] = key
        out['GIT_CONFIG_VALUE_'+str(i)] = value
    return out


@contextmanager
def fixed_git_settings():
    # The verified source helpers also use /usr/bin/git internally.
    old = {key: value for key, value in os.environ.items() if key.startswith('GIT_')}
    for key in old:
        del os.environ[key]
    os.environ.update(git_values())
    try:
        yield
    finally:
        for key in tuple(os.environ):
            if key.startswith('GIT_'):
                del os.environ[key]
        os.environ.update(old)


def fetch_git(repo, where, head, *, run=subprocess.run, prior=False):
    require(repo in (SERVICES, WORKERS) and valid(SHA, head), 'GIT_OBJECTS_UNVERIFIED')
    # Fresh private object database. No checkout, refs from user input, remotes,
    # GitHub credentials, template hooks, global gitconfig, LFS or submodules.
    env = {'PATH':'/usr/bin:/bin','HOME':str(where.parent),'LANG':'C','LC_ALL':'C',**git_values()}

    def command(*args):
        result = run(['/usr/bin/git', *args], cwd=where.parent, env=env, check=False,
                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=70)
        require(result.returncode==0, 'GIT_OBJECTS_UNVERIFIED')
        return result.stdout

    require(not prior or repo==WORKERS,'GIT_OBJECTS_UNVERIFIED')
    source_ref, local_ref=(head,'refs/heads/observed-worker') if prior else ('refs/heads/main','refs/heads/canonical-main')
    command('init','--bare','--template=',str(where))
    command('-C',str(where),'fetch','--depth=1','--no-tags','--no-recurse-submodules',
            'https://github.com/'+repo+'.git',source_ref+':'+local_ref)
    require(command('-C',str(where),'rev-parse','--verify',local_ref+'^{commit}')
            == (head+'\n').encode(), 'GIT_OBJECTS_UNVERIFIED')
    return where


PID = re.compile(r'^\s*pid = ([1-9][0-9]*)\s*$', re.MULTILINE)


class ReadonlyHost:
    """Expose only the one read-only launchctl action trusted staging needs."""
    def launch_report(self, label):
        require(re.fullmatch(r'com\.(?:vinus|projectbs)\.[A-Za-z0-9.-]{1,150}', label),
                'HELPER_BUNDLE_UNVERIFIED')
        job = f'gui/{os.getuid()}/{label}'
        p = subprocess.run(['/bin/launchctl','print',job], stdin=subprocess.DEVNULL, capture_output=True,
                           timeout=15, env={'PATH':'/bin:/usr/bin','LANG':'C'})
        if p.returncode!=0 or len(p.stdout)>128*1024:
            return None, ''
        report = p.stdout.decode('utf-8','strict')
        pids = PID.findall(report)
        return (int(pids[0]),report) if len(pids)==1 else (None,'')


def stat_db(path):
    s = path.lstat()
    require(path.resolve(strict=True)==path and stat.S_ISREG(s.st_mode) and s.st_nlink==1 and
            s.st_uid==os.getuid() and not s.st_mode & 0o022, 'WORKER_IDLE_UNPROVEN')
    return s


def running_count(path, worker):
    st = stat_db(path)
    require(stat.S_IMODE(st.st_mode)==0o600, 'WORKER_IDLE_UNPROVEN')
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as f:
        require(same_inode(st,os.fstat(f.fileno())), 'WORKER_IDLE_UNPROVEN')
        header = f.read(100)
    require(len(header)==100 and header.startswith(b'SQLite format 3\0'), 'WORKER_IDLE_UNPROVEN')
    # No immutable=1: it ignores committed WAL. If WAL is in use, never open
    # without the existing private companions (which can be created by SQLite).
    if header[18:20]==b'\x02\x02':
        for ext in ('-wal','-shm'):
            stat_db(path.with_name(path.name+ext))
    elif header[18:20]!=b'\x01\x01':
        raise Refusal('WORKER_IDLE_UNPROVEN')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.5,isolation_level=None)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA trusted_schema=OFF')
        row = db.execute("SELECT EXISTS(SELECT 1 FROM workflow_tasks WHERE worker_type=? AND status='running')",
                         (worker,)).fetchone()
    require(same_inode(st,stat_db(path)) and row in ((0,),(1,)), 'WORKER_IDLE_UNPROVEN')
    return row[0]


def worker_loaded(deps, home, host, target, expected, *, idle=False):
    if target not in WORKER_TARGETS or not valid(DIGEST, expected):
        return False
    try:
        cat = deps.target_specs[target]
        selector = home/'Library'/'LaunchAgents'/(cat.label+'.plist')
        prior = read_private(selector,65536)
        if sha(prior)!=expected:
            return False
        row = plistlib.loads(prior)
        args, wd = row['ProgramArguments'], row['WorkingDirectory']
        database = home/'Workspaces/Workers/data/workflow-planner/workflow.sqlite'
        if (row.get('Label')!=cat.label or type(args) is not list or not all(isinstance(a,str) for a in args)
                or type(wd) is not str or args.count('--db')!=1 or args.index('--db')+1>=len(args)
                or args[args.index('--db')+1]!=str(database)):
            return False
        pid, report = host.launch_report(cat.label)
        if not pid or not deps.loaded_selector(report,args,wd) or (idle and running_count(database,target.removeprefix('worker-'))):
            return False
        confirmed, latest = host.launch_report(cat.label)
        return bool(confirmed==pid and deps.loaded_selector(latest,args,wd) and
                    read_private(selector,65536)==prior)
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error,subprocess.SubprocessError):
        return False



def worker_idle(deps,home,host,target,expected):
    return worker_loaded(deps,home,host,target,expected,idle=True)


# The existing image worker reports heartbeat only after its provider probe.
# Other roles need their own equivalent recovery proof before enabling retry.
IMAGE_RETRY_TARGET = 'worker-image'
IMAGE_RETRY_LABEL = 'com.vinus.workflow.image-worker'


def loaded_image_selector(report,args,working_directory):
    lines=report.splitlines()
    try:
        start=lines.index('\targuments = {')+1
        end=lines.index('\t}',start)
    except ValueError:
        return False
    return (lines[start:end]==['\t\t'+p for p in args]
            and lines.count('\tworking directory = '+working_directory)==1)


def current_worker_idle(home,target,selector_sha):
    if target not in WORKER_TARGETS:return False
    deps=SimpleNamespace(target_specs={target:SimpleNamespace(
        label='com.vinus.workflow.'+target.removeprefix('worker-')+'-worker')},loaded_selector=loaded_image_selector)
    return worker_idle(deps,home,ReadonlyHost(),target,selector_sha)


def image_heartbeat():
    conn=http.client.HTTPConnection('127.0.0.1',8090,timeout=2)
    try:
        conn.request('GET','/api/service-status')
        response=conn.getresponse()
        raw=response.read(256*1024+1)
        require(response.status==200 and len(raw)<=256*1024,'WORKER_RETRY_BASELINE_UNPROVEN')
        return json.loads(raw)['services']
    finally:
        conn.close()


def image_retry_baseline(home,target,selector_sha,observed_sha,finished_at,*,host=None,heartbeat=None,clock=None):
    """Reprove the pre-failure installed image worker without writing or claiming."""
    if target!=IMAGE_RETRY_TARGET or not valid(DIGEST,selector_sha) or not valid(SHA,observed_sha):
        return False
    host=ReadonlyHost() if host is None else host
    heartbeat=image_heartbeat if heartbeat is None else heartbeat
    clock=(lambda: datetime.now(UTC)) if clock is None else clock
    home=Path(home)
    try:
        path=home/'Library/LaunchAgents'/(IMAGE_RETRY_LABEL+'.plist')
        raw=read_private(path,65536)
        if sha(raw)!=selector_sha:return False
        selector=plistlib.loads(raw)
        entry=Path(selector['ProgramArguments'][1])
        releases=home/'Workspaces/Workers/code/releases'
        relative=entry.relative_to(releases).parts
        match=re.fullmatch(r'workflows-github-([a-f0-9]{7,40})',relative[0]) if relative else None
        if (not match or relative[1:]!=('image','image-cli.mjs') or not observed_sha.startswith(match[1])
                or entry.resolve(strict=True)!=entry or not entry.is_file()):return False
        environment=selector['EnvironmentVariables']
        host_config=Path(environment['WORKFLOW_IMAGE_HOST_CONFIG'])
        host_digest=environment['WORKFLOW_IMAGE_HOST_CONFIG_SHA256']
        if (not host_config.is_absolute() or host_config.resolve(strict=True)!=host_config
                or sha(read_private(host_config,128*1024))!=host_digest):return False
        deps=SimpleNamespace(target_specs={target:SimpleNamespace(label=IMAGE_RETRY_LABEL)},
                             loaded_selector=loaded_image_selector)
        if not worker_idle(deps,home,host,target,selector_sha):return False
        cutoff=max(datetime.fromisoformat(finished_at),datetime.fromtimestamp(path.stat().st_mtime,UTC),
                   datetime.fromtimestamp(host_config.stat().st_mtime,UTC))
        rows=[row for row in heartbeat() if row.get('serverName')=='image-worker']
        if len(rows)!=1 or rows[0].get('state')!='connected':return False
        beat=datetime.fromisoformat(rows[0]['lastReceivedAt'])
        current=clock()
        return (cutoff<=beat<=current and (current-beat).total_seconds()<=125
                and read_private(path,65536)==raw
                and sha(read_private(host_config,128*1024))==host_digest)
    except (OSError,ValueError,KeyError,IndexError,TypeError,AttributeError,http.client.HTTPException):
        return False


def row(state, *, head=None, code=None):
    out = {'state':state if state in STATES else 'failed'}
    if valid(SHA, head):
        out['sha'] = head
    if isinstance(code, str) and code in AUTH|PENDING|BLOCKED:
        out['code'] = code
    return out


def error_row(exc, head=None):
    code = exc.args[0] if len(exc.args)==1 and isinstance(exc.args[0],str) else None
    if code in AUTH:
        return row('auth_required',head=head,code=code)
    if code in PENDING:
        return row('pending',head=head,code=code)
    return row('blocked' if code in BLOCKED else 'failed',head=head,code=code if code in BLOCKED else 'PREP_FAILED')


def convert(result, head):
    if type(result) is not dict or type(result.get('state')) is not str:
        return row('failed',head=head,code='PREP_FAILED')
    state = {'registered':'ready','already_ready':'ready'}.get(result['state'], result['state'])
    return row(state,head=head,code=result.get('code'))


def run_once(deps, home, control, *, fetch=fetch_git, prior_fetch=fetch_git, host=None):
    host = host if host is not None else ReadonlyHost()
    states = {FILEX:row('not_configured',code='FILEX_PREPARER_NOT_PROVISIONED')}
    # Candidate source helpers independently recheck canonical main under their
    # own existing readiness/CAS locks. A newer head must never be registered.
    with tempfile.TemporaryDirectory(prefix='.candidate-objects-',dir=control) as root, fixed_git_settings():
        filex_git = None
        for repo, catalog in ((SERVICES,SERVICE_TARGETS),(WORKERS,WORKER_TARGETS)):
            head = None
            try:
                main = lambda: deps.canonical(repo)
                head = main()
                require(valid(SHA,head), 'GITHUB_TARGET_EVIDENCE_INVALID')
                ev = deps.acquire(repo)
                require(type(ev) is dict and set(ev)=={'head','workflow','run','artifact','raw','verified'} and
                        type(ev['verified']) is dict, 'GITHUB_TARGET_EVIDENCE_INVALID')
                require(ev['head']==head, 'MAIN_ADVANCED')
                git = fetch(repo,Path(root)/repo.split('/')[1],head)
                def assert_current(expected):
                    require(expected==head and main()==head,'MAIN_ADVANCED')
                assert_current(head)
                if repo==SERVICES:
                    filex_git = (head,git,ev['verified'])
                    def filex_ready(expected):
                        active = getattr(deps,'filex_active',None)
                        return bool(expected==head and active and active(home,control,git,head) is True)
                    result = deps.prepare_services(ev['verified'],git,home,control,assert_current=assert_current,
                                                   filex_ready=filex_ready,host=host)
                else:
                    history={}
                    def prior_repo(sha):
                        require(valid(SHA,sha),'GIT_OBJECTS_UNVERIFIED')
                        if sha not in history:
                            history[sha]=prior_fetch(WORKERS,Path(root)/('worker-prior-'+sha),sha,prior=True)
                        return history[sha]
                    package = deps.prepare_workers({k:v for k,v in ev.items() if k!='verified'},repo=git,
                        home=home,control=control,read_main=main,
                        idle=lambda target,prior:worker_idle(deps,home,host,target,prior),trusted_deploy=control/'bin',
                        retry_probe=lambda target,pin,prior,finished:image_retry_baseline(
                            home,target,pin,prior,finished,host=host),
                        loaded=lambda target,prior:worker_loaded(deps,home,host,target,prior),prior_repo=prior_repo)
                    require(type(package) is dict and package.get('sha')==head,'GITHUB_TARGET_EVIDENCE_INVALID')
                    result = package['targets']
                require(type(result) is dict and set(result)==set(catalog),'GITHUB_TARGET_EVIDENCE_INVALID')
                for name in catalog:
                    item = result[name]
                    if type(item) is not dict or item.get('sha',head)!=head or item.get('target',name)!=name:
                        states[name] = row('failed',head=head,code='PREP_FAILED')
                    else:
                        states[name] = convert(item,head)
                if repo==SERVICES:
                    states[FILEX]['sha']=head
            except Exception as exc:
                for name in catalog:
                    states[name] = error_row(exc,head)
        filex_head = None
        try:
            config = getattr(deps,'filex_config',None)
            if config and config(control) is not None:
                filex_head = deps.canonical(SERVICES)
                require(valid(SHA,filex_head), 'GITHUB_TARGET_EVIDENCE_INVALID')
                require(callable(deps.prepare_filex), 'FILEX_RUNTIME_HELPERS_UNPROVISIONED')
                repo = filex_git[1] if filex_git and filex_git[0]==filex_head else fetch(
                    SERVICES,Path(root)/'filex-ngs-services',filex_head)
                def assert_filex_current(expected):
                    require(expected==filex_head and deps.canonical(SERVICES)==filex_head,'MAIN_ADVANCED')
                options={'services':filex_git[2]} if (getattr(deps,'filex_unified',False)
                            and filex_git and filex_git[0]==filex_head) else {}
                receipt = deps.prepare_filex(repo,home,control,filex_head,assert_current=assert_filex_current,**options)
                require(type(receipt) is dict and receipt.get('target')==FILEX and receipt.get('sha')==filex_head,
                        'GITHUB_TARGET_EVIDENCE_INVALID')
                states[FILEX] = convert(receipt,filex_head)
        except Exception as exc:
            states[FILEX] = error_row(exc,filex_head)
        head = None
        try:
            head = deps.local_main()
            require(valid(SHA,head),'GITHUB_TARGET_EVIDENCE_INVALID')
            def assert_current_local(expected):
                require(expected==head and deps.local_main()==head,'MAIN_ADVANCED')
            receipt = deps.prepare_relay(control,home,head,assert_current=assert_current_local,host=host)
            require(type(receipt) is dict and receipt.get('target')==RELAY and receipt.get('sha')==head,
                    'GITHUB_TARGET_EVIDENCE_INVALID')
            states[RELAY] = convert(receipt,head)
        except Exception as exc:
            states[RELAY] = error_row(exc,head)
    return states


def load_deps(parent):
    require(all(name not in sys.modules for name in (*HELPERS,*FILEX_HELPERS,FILEX_UNIFIED_HELPER)), 'HELPER_BUNDLE_UNVERIFIED')
    # Keep the finder active throughout this single invocation for lazy support
    # imports. Do not put the private helper or candidate directories in sys.path.
    finder = PinnedHelpers(parent)
    sys.meta_path.insert(0,finder)
    from github_actions_keychain import KeychainGitHubEvidence
    from github_target_candidate_evidence import FilexGitHubEvidence, canonical_target_main, acquire_target_candidate
    from mac_target_apply import TARGETS as target_specs
    from planner_cutover import loaded_selector
    from prepare_ngs_local_once import main_head
    from prepare_service_candidates import prepare_service_candidates, prepare_rs_mcp_candidate
    from prepare_worker_candidates import prepare_worker_candidates
    from prepare_filex_supervised import config as filex_config, prepare_filex_candidate
    from prepare_filex_writer import IsolatedDocker

    filex_prep = filex_live = None
    if all(name in finder.by_name for name in FILEX_HELPERS):
        def filex_prep(repo,home,control,head,*,assert_current,services=None):
            from filex_writer_apply import filex_prior
            return prepare_filex_candidate(FilexGitHubEvidence(),repo,home,control,head,
                assert_current=assert_current,read_prior=filex_prior,docker=IsolatedDocker(home),services=services)

        def filex_live(home,control,repo,head):
            from filex_writer_active import filex_active
            return filex_active(home,control,repo,head)

    return SimpleNamespace(canonical=canonical_target_main,acquire=acquire_target_candidate,
        local_main=lambda:main_head(KeychainGitHubEvidence()),prepare_services=prepare_service_candidates,
        prepare_workers=prepare_worker_candidates,prepare_relay=prepare_rs_mcp_candidate,
        target_specs=target_specs,loaded_selector=loaded_selector,
        filex_config=filex_config,prepare_filex=filex_prep,filex_active=filex_live,
        filex_unified=FILEX_UNIFIED_HELPER in finder.by_name)


def main():
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    os.environ['HOME'] = str(home)
    control = home/'Workspaces/OPS/runtime/deploy-control'
    require(len(sys.argv)==1 and sys.flags.isolated and sys.dont_write_bytecode and
            Path(__file__)==control/'bin/prepare_ops_candidates_once.py',
            'HELPER_BUNDLE_UNVERIFIED')
    os.umask(0o077)
    private_dir(control)
    with private_job_lock(control) as acquired:
        if not acquired:
            return
        try:
            states = run_once(load_deps(control/'bin'),home,control)
        except Exception:
            states = {key:row('blocked',code='HELPER_BUNDLE_UNVERIFIED') for key in TARGETS}
        install_status(control,states,datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'))


if __name__=='__main__':
    try:
        main()
    except Exception:
        # Do not emit paths, subprocess details, headers or tracebacks from launchd.
        raise SystemExit(1) from None
