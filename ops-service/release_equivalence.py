"""Owner-private proof that a verified candidate uses the already-running runtime."""
import hashlib
import json
import os
from pathlib import Path
import stat

from deploy_control import KEY, bounded_private, is_sha, stamp


SCHEMA='ops.deploy-target-equivalent.v1'
SELECTORS={
    'ngs-local':'com.vinus.ngs.local-mcp',
    'request-service':'com.vinus.ngs.request-service',
    'workflow-planner':'com.vinus.workflow.planner',
    'backend-stack':'com.projectbs.backend-stack',
    'rs-mcp':'com.vinus.ngs.rs-mcp',
    'worker-document-2':'com.vinus.workflow.document-worker-2',
    **{f'worker-image-{slot}':f'com.vinus.workflow.image-worker-{slot}' for slot in (2,3,4)},
    **{f'worker-{name}':f'com.vinus.workflow.{name}-worker'
       for name in ('document','image','animation','postprocess','audio','file')},
}
FIELDS={'schemaVersion','target','sha','observedSha','artifactSha256','runnerSha256',
        'selectorSha256','runtimeTreeSha256','verifiedAt'}


def fail():
    raise ValueError('OPS_DEPLOY_EQUIVALENCE_INVALID')


def actual_selector_sha(target, home=None):
    """Only an allowlisted Mac job selector; no supplied path or opaque target."""
    lab=SELECTORS.get(target)
    if lab is None:fail()
    owner=Path.home() if home is None else Path(home)
    base=owner/'Library/LaunchAgents'
    path=base/(lab+'.plist')
    if base.resolve()!=base or path.parent!=base:fail()
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as f:
        s=os.fstat(f.fileno())
        if (not stat.S_ISREG(s.st_mode) or stat.S_IMODE(s.st_mode) not in (0o600,0o644)
            or s.st_uid!=os.getuid() or s.st_nlink!=1 or not 0<s.st_size<=128*1024):
            fail()
        raw=f.read(128*1024+1)
        post=os.fstat(f.fileno())
        if (len(raw)!=s.st_size or post.st_mtime_ns!=s.st_mtime_ns or
            path.lstat().st_ino!=s.st_ino):fail()
    return hashlib.sha256(raw).hexdigest()


def equivalent_release(directory,target,ready,observation,*,selector_sha=actual_selector_sha):
    """Never invent an observation or execute a no-change candidate.

    Trusted local preparation checks authenticated artifact evidence and full
    immutable runtime/file mode equivalence. The cycle independently validates
    readiness and the executable pin before reaching this function. This
    proof is bound to both and ceases to be current when the selector changes.
    """
    try:
        record=json.loads(bounded_private(Path(directory)/'equivalent.json',4096))
    except FileNotFoundError:
        return False
    if (type(record) is not dict or set(record)!=FIELDS or record['schemaVersion']!=SCHEMA or
        record['target']!=target or target not in SELECTORS or not is_sha(record['sha']) or
        not is_sha(record['observedSha']) or record['sha']==record['observedSha'] or
        any(type(record[k]) is not str or not KEY.fullmatch(record[k]) for k in
            ('artifactSha256','runnerSha256','selectorSha256','runtimeTreeSha256'))):
        fail()
    stamp(record['verifiedAt'])
    if record['sha']!=ready['sha']:
        return False # A proof for a previous candidate cannot affect a newer one.
    if (not observation or record['observedSha']!=observation['sha'] or
        record['artifactSha256']!=ready['artifactSha256'] or record['runnerSha256']!=ready['runnerSha256'] or
        record['selectorSha256']!=selector_sha(target)):
        fail()
    return True
