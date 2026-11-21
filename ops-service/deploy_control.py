"""Owner-local authorization for the single OPS deployment cycle."""
import datetime
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
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


class DeployControl:
    def __init__(self, root, *, cycle=None, clock=time.monotonic):
        if cycle is None:
            from deploy_cycle import DeploymentCycle
            cycle = DeploymentCycle(root)
        self.root = Path(root)
        self.cycle, self.clock = cycle, clock
        self.lock = threading.Lock()
        self.sessions = {}

    def _proof(self):
        secure_directory(self.root)
        key = bounded_private(self.root/'owner-key', 65).decode('ascii').removesuffix('\n')
        if not KEY.fullmatch(key):
            raise ValueError('INVALID_OWNER_KEY')
        return key

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

    def status(self, sid):
        with self.lock:
            try:
                csrf=self._session(sid)
                cycle=self.cycle.status()
                return {'csrf':csrf,'cycle':cycle,'observed':self._baseline()}
            except (OSError,ValueError):
                reject(503,'OPS_DEPLOY_NOT_CONFIGURED')

    def wake(self, sid, csrf, selection):
        with self.lock:
            try:
                self._session(sid,csrf)
                return self.cycle.trigger(selection)
            except (OSError,ValueError):
                reject(503,'OPS_DEPLOY_NOT_CONFIGURED')


def from_environment(env=os.environ, *, root=None):
    root = Path(root) if root is not None else Path.home()/'Workspaces/OPS/runtime/deploy-control'
    if env.get('NGS_OPS_DEPLOY_ENABLED')!='1':
        if 'NGS_OPS_DEPLOY_ENABLED' in env:
            return None
        try:
            secure_directory(root)
            if bounded_private(root/'manual-enabled',2)!=b'1\n':
                return None
        except (OSError,ValueError):
            return None
    return DeployControl(root)
