"""Owner-local gate for one already prepared NGS release; no external auth or Git execution."""
import datetime
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import tempfile
import threading
import time


SHA = re.compile(r'[0-9a-f]{40}\Z')
KEY = re.compile(r'[0-9a-f]{64}\Z')
DECIMAL = re.compile(r'[1-9][0-9]{0,19}\Z')
STAMP = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\Z')
COOKIE = 'ngs_ops_deploy'
TTL = 900


class Rejected(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


def reject(status, code):
    raise Rejected(status, code)


def is_sha(value):
    return type(value) is str and bool(SHA.fullmatch(value))


def stamp(value):
    if type(value) is not str or not STAMP.fullmatch(value):
        raise ValueError('INVALID_DEPLOY_TIMESTAMP')
    return datetime.datetime.fromisoformat(value)


def secure_directory(path):
    attrs = path.lstat()
    if path.resolve() != path or attrs.st_uid != os.getuid() or not stat.S_ISDIR(attrs.st_mode) or stat.S_IMODE(attrs.st_mode) != 0o700:
        raise OSError('UNTRUSTED_DEPLOY_DIRECTORY')


def bounded_private(path, limit):
    flag = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(path, flag), 'rb') as f:
        attrs = os.fstat(f.fileno())
        if (not stat.S_ISREG(attrs.st_mode) or attrs.st_uid != os.getuid() or attrs.st_nlink != 1
            or stat.S_IMODE(attrs.st_mode) != 0o600 or not 0 < attrs.st_size <= limit):
            raise OSError('UNTRUSTED_DEPLOY_FILE')
        data = f.read(limit+1)
        post = os.fstat(f.fileno())
        if (len(data) != attrs.st_size or attrs.st_mtime_ns != post.st_mtime_ns or
            post.st_size != attrs.st_size or path.lstat().st_ino != attrs.st_ino):
            raise OSError('DEPLOY_FILE_CHANGED')
        return data


def fixed_mac_runner(root, sha):
    """The only action-time input is a prepared Git commit SHA."""
    executable = root/'bin/ngs-mac-apply'
    secure_directory(root/'bin')
    st = executable.lstat()
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_nlink != 1
        or stat.S_IMODE(st.st_mode) not in (0o500,0o700)):
        raise OSError('UNTRUSTED_MAC_ADAPTER')
    with tempfile.TemporaryFile(dir=root) as out:
        p = subprocess.run([str(executable), sha], cwd=root, env={
            'HOME':str(Path.home()), 'PATH':'/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin',
        }, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.DEVNULL, check=False)
        out.seek(0)
        payload = out.read(4097)
        if p.returncode or len(payload)>4096:
            raise OSError('MAC_VERIFY_FAILED')
        return json.loads(payload)


class DeployControl:
    """Private verifier publishes prepared.json; it is never written through OPS."""
    def __init__(self, root, *, run_mac=fixed_mac_runner, clock=time.monotonic):
        self.root = Path(root)
        self.run_mac, self.clock = run_mac, clock
        self.lock = threading.Lock()
        self.sessions = {}
        self.active = None

    def _proof(self):
        secure_directory(self.root)
        secure_directory(self.root/'jobs')
        key = bounded_private(self.root/'owner-key', 65).decode('ascii').removesuffix('\n')
        if not KEY.fullmatch(key):
            raise ValueError('INVALID_OWNER_KEY')
        return key

    def _prepared(self):
        try:
            value=json.loads(bounded_private(self.root/'prepared.json',2048))
        except FileNotFoundError:
            return None
        if (not isinstance(value,dict) or set(value)-{'space'}!={'schemaVersion','sha','verifiedAt','mac','provenance'}
            or value['schemaVersion']!='ops.ngs-prepared-deploy.v1' or not is_sha(value['sha'])
            or type(value['verifiedAt']) is not str or not STAMP.fullmatch(value['verifiedAt'])
            or value['mac']!={'ready':True} or type(value['mac']['ready']) is not bool):
            raise ValueError('INVALID_PREPARED')
        datetime.datetime.fromisoformat(value['verifiedAt'])
        evidence=value['provenance']
        if (not isinstance(evidence,dict) or set(evidence)!={'runId','runAttempt','artifactId','archiveSha256'} or
            any(type(evidence[k]) is not str or not DECIMAL.fullmatch(evidence[k]) for k in ('runId','runAttempt','artifactId')) or
            type(evidence['archiveSha256']) is not str or not KEY.fullmatch(evidence['archiveSha256'])):
            raise ValueError('INVALID_RELEASE_PROVENANCE')
        if 'space' in value:
            item=value['space']
            if (not isinstance(item,dict) or set(item)!={'sourceSha','packageBaseSha','archiveSha256'}
                or item['sourceSha']!=value['sha'] or not is_sha(item['packageBaseSha'])
                or type(item['archiveSha256']) is not str or not KEY.fullmatch(item['archiveSha256'])):
                raise ValueError('INVALID_SPACE_PACKAGE_PROVENANCE')
        return value

    def _adapter_ready(self):
        if self.run_mac is not fixed_mac_runner:
            return True
        try:
            path=self.root/'bin/ngs-mac-apply'
            secure_directory(path.parent)
            s=path.lstat()
            return (stat.S_ISREG(s.st_mode) and s.st_uid==os.getuid() and s.st_nlink==1 and
                    stat.S_IMODE(s.st_mode) in (0o500,0o700))
        except OSError:
            return False

    def begin(self, proof):
        try:
            key=self._proof()
        except (OSError,ValueError):
            reject(503,'OPS_DEPLOY_NOT_CONFIGURED')
        if type(proof) is not str or not KEY.fullmatch(proof) or not hmac.compare_digest(key,proof):
            reject(403,'OPS_DEPLOY_OWNER_REQUIRED')
        with self.lock:
            now=self.clock()
            self.sessions={k:v for k,v in self.sessions.items() if v[1]>now}
            if len(self.sessions)>=16:
                self.sessions.pop(next(iter(self.sessions)))
            sid,csrf=secrets.token_hex(32),secrets.token_hex(32)
            self.sessions[sid]=(csrf,now+TTL,key)
            return sid

    def _session(self, sid, csrf=None):
        key=self._proof()
        row=self.sessions.get(sid)
        if not row or row[1]<=self.clock() or not hmac.compare_digest(row[2],key):
            reject(401,'OPS_DEPLOY_OWNER_REQUIRED')
        if csrf is not None and (not KEY.fullmatch(csrf) or not hmac.compare_digest(row[0],csrf)):
            reject(403,'OPS_DEPLOY_CSRF_DENIED')
        return row[0]

    def _job(self, sha):
        try:
            item=json.loads(bounded_private(self.root/'jobs'/f'{sha}.json',512))
        except FileNotFoundError:
            return None
        if (not isinstance(item,dict) or set(item)-{'deployedAt'}!={'sha','state'} or item['sha']!=sha or
            item['state'] not in ('running','deployed','failed') or
            ('deployedAt' in item) != (item['state']=='deployed')):
            raise ValueError('INVALID_DEPLOY_JOB')
        if 'deployedAt' in item:stamp(item['deployedAt'])
        return item

    def _baseline(self):
        try:
            value=json.loads(bounded_private(self.root/'observed.json',2048))
        except FileNotFoundError:
            return {'local':None,'space':None}
        if (not isinstance(value,dict) or set(value)!={'schemaVersion','local','space'} or
            value['schemaVersion']!='ops.ngs-observed-deployments.v1'):
            raise ValueError('INVALID_OBSERVED_RELEASES')
        for target in ('local','space'):
            item=value[target]
            if item is None:continue
            expected={'sha','deployedAt'} | ({'providerSha','deploymentId'} if target=='space' else set())
            if not isinstance(item,dict) or set(item)!=expected or not is_sha(item['sha']):
                raise ValueError('INVALID_OBSERVED_RELEASE')
            stamp(item['deployedAt'])
            if target=='space' and (not is_sha(item['providerSha']) or type(item['deploymentId']) is not str or not
                    re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}',item['deploymentId'])):
                raise ValueError('INVALID_SPACE_RECEIPT')
        return {'local':value['local'],'space':value['space']}

    def _jobs(self):
        with os.scandir(self.root/'jobs') as entries:
            for index, entry in enumerate(entries):
                if index>=4096:raise ValueError('DEPLOY_RECEIPT_LIMIT')
                if not entry.name.endswith('.json') or not is_sha(entry.name[:-5]):continue
                job=self._job(entry.name[:-5])
                if job:yield job

    def _last_local(self, observed, jobs):
        # Receipts are written atomically per SHA. Derive the latest completed
        # observation without a possible split/crash between a job and pointer.
        choices=([observed] if observed else [])+[
            {'sha':job['sha'],'deployedAt':job['deployedAt']}
            for job in jobs if job['state']=='deployed']
        if not choices:return None
        newest=max(item['deployedAt'] for item in choices)
        items=[item for item in choices if item['deployedAt']==newest]
        return items[0] if len({item['sha'] for item in items})==1 else None

    def _stored(self, path, payload, *, first=False):
        raw=(json.dumps(payload,separators=(',',':'))+'\n').encode()
        tmp_fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.job-')
        try:
            with os.fdopen(tmp_fd,'wb') as f:
                f.write(raw);f.flush();os.fsync(f.fileno())
            if first:
                try:
                    # Atomic publication; the temporary hardlink disappears before any reader unlocks.
                    os.link(tmp,path,follow_symlinks=False)
                except FileExistsError:
                    return False
            else:
                os.replace(tmp,path)
            return True
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _run(self, sha):
        try:
            result=self.run_mac(self.root,sha)
            state='deployed' if result=={'sha':sha,'target':'mac','state':'deployed','health':'verified'} else 'failed'
        except Exception:
            state='failed'
        finally:
            with self.lock:
                try:
                    record={'sha':sha,'state':state}
                    if state=='deployed':
                        record['deployedAt']=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                    self._stored(self.root/'jobs'/f'{sha}.json',record)
                except (OSError,UnboundLocalError):
                    pass # A restart/unpersisted receipt is shown as unknown, never retried automatically.
                finally:
                    self.active=None

    def _display(self):
        candidate=self._prepared()
        observed=self._baseline()
        jobs=list(self._jobs())
        observed['local']=self._last_local(observed['local'],jobs)
        sha=candidate['sha'] if candidate else None
        job=self._job(sha) if sha else None
        state=job['state'] if job else None
        if self.active is not None and self.active!=sha:
            state='running'
        if self.active is None and any(item['state']=='running' for item in jobs):
            state='unknown'
        if state is None:
            state=('ready' if self._adapter_ready() else 'not_configured') if candidate else 'not_prepared'
        space_state=('pending' if candidate and 'space' in candidate else 'not_configured')
        compare=None
        if observed['local'] and observed['space'] and state not in ('running','failed','unknown'):
            compare='match' if observed['local']['sha']==observed['space']['sha'] else 'mismatch'
        return {'sha':sha,'verifiedAt':candidate['verifiedAt'] if candidate else None,
                'mac':state,'space':space_state,'observed':observed,'comparison':compare,
                'canDeploy':state=='ready' and self.active is None,'canDeploySpace':False,'canDeployAll':False}

    def status(self, sid):
        with self.lock:
            try:
                csrf=self._session(sid)
                return {**self._display(),'csrf':csrf}
            except (OSError,ValueError):
                reject(503,'OPS_DEPLOY_NOT_CONFIGURED')

    def request(self, sid, csrf, sha, target):
        with self.lock:
            try:
                self._session(sid,csrf)
                if target!='local':reject(503,'OPS_DEPLOY_SPACE_PUBLISH_UNAVAILABLE')
                candidate=self._prepared()
                if not candidate or candidate['sha']!=sha:
                    reject(409,'OPS_DEPLOY_RELEASE_NOT_PREPARED')
                # Replay the existing acknowledgement, never execute a SHA twice.
                if self._job(sha) is not None:
                    return self._display()
                if not self._adapter_ready():
                    reject(503,'OPS_DEPLOY_ADAPTER_UNAVAILABLE')
                if self.active is not None:
                    reject(409,'OPS_DEPLOY_ALREADY_RUNNING')
                if any(item['state']=='running' for item in self._jobs()):
                    reject(409,'OPS_DEPLOY_PREVIOUS_RESULT_UNKNOWN')
                self._display() # Check evidence shape before committing execution.
                path=self.root/'jobs'/f'{sha}.json'
                if self._stored(path,{'sha':sha,'state':'running'},first=True):
                    self.active=sha
                    threading.Thread(target=self._run,args=(sha,),daemon=True).start()
                return self._display()
            except (OSError,ValueError):
                reject(503,'OPS_DEPLOY_NOT_CONFIGURED')


def from_environment(env=os.environ):
    if env.get('NGS_OPS_DEPLOY_ENABLED')!='1':
        return None
    return DeployControl(Path.home()/'Workspaces/NGS/runtime/deploy-control')
