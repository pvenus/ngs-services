"""Live renderer proof, read-only, owner-private on the same trusted Mac."""
import json
import os
import stat
import time
from pathlib import Path

READY_FILE=Path.home()/'Workspaces/Workers/runtime/prop-animation/prop-animation-ready.json'
MAX_AGE_MS=120_000
SCHEMA='workflow.prop-animation-renderer-ready.v1'
PROVIDER='wan-ti2v-q8_0'

def renderer_ready(path=READY_FILE,*,now=None,schema=SCHEMA):
    try:
        p=Path(path)
        if not p.is_absolute() or p.resolve()!=p:return False
        parent=p.parent.stat()
        if parent.st_uid!=os.getuid() or stat.S_IMODE(parent.st_mode)!=0o700:return False
        def pairs(rows):
            result=dict(rows)
            if len(result)!=len(rows):raise ValueError()
            return result
        with os.fdopen(os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as f:
            s=os.fstat(f.fileno())
            if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.getuid() or s.st_nlink!=1 or \
               stat.S_IMODE(s.st_mode)!=0o600 or not 0<s.st_size<=512:return False
            raw=f.read(513)
            if len(raw)!=s.st_size or os.fstat(f.fileno()).st_mtime_ns!=s.st_mtime_ns:return False
        v=json.loads(raw,object_pairs_hook=pairs)
        if type(v) is not dict or set(v)!= {'schemaVersion','provider','checkedAt'} or \
           v['schemaVersion']!=schema or v['provider']!=PROVIDER or type(v['checkedAt']) is not int:return False
        clock=time.time_ns()//1_000_000 if now is None else now
        return 0<=clock-v['checkedAt']<=MAX_AGE_MS
    except (OSError,ValueError,RuntimeError):return False
