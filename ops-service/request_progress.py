"""Exact owner-local, read-only projection of the dedicated workflow planner."""
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

WORKFLOW_DB = Path.home() / 'Workspaces/Workers/data/workflow-planner/workflow.sqlite'
REQUEST_ID = re.compile(r'request\.official\.[a-f0-9]{64}')
TASK_LABEL = re.compile(r'[a-z0-9][a-z0-9._-]{0,99}')
RUN_STATES = {'awaiting_rs', 'planned', 'failed', 'not_claimable'}
TASK_STATES = {'pending', 'running', 'completed', 'failed'}
WORKERS = {'file', 'document', 'image', 'animation', 'postprocess', 'audio'}
# Upper bound implied by one accepted canonical payload, not a selection cap.
# A valid motion record always carries the exact keys, at least one byte of
# preset ID, the shortest pose ("idle"), and all 64 hash digits. Each adds 16
# tasks (animation + frames, eight directions). Full plans add at most 25
# setup tasks; older workflows fit inside the same projection.
CANONICAL_PAYLOAD_BYTES = 64 * 1024
MIN_MOTION_RECORD_BYTES = len(json.dumps(
    {'presetId':'a','posePreset':'idle','assetSetSha256':'a'*64},
    separators=(',',':')).encode('utf-8'))
MAX_TASKS = 25 + 16 * (CANONICAL_PAYLOAD_BYTES // MIN_MOTION_RECORD_BYTES)


class ProgressUnavailable(Exception):
    pass


def query_id(query):
    # One exact parameter; rejects absent, duplicated, encoded or extra tokens.
    prefix = 'requestId='
    if not query.startswith(prefix) or not REQUEST_ID.fullmatch(query[len(prefix):]):
        raise ValueError('INVALID_REQUEST_ID')
    return query[len(prefix):]


def _identity(path):
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ProgressUnavailable()
    parent = path.parent.lstat()
    entry = path.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or stat.S_IMODE(parent.st_mode) != 0o700
        or not stat.S_ISREG(entry.st_mode) or stat.S_IMODE(entry.st_mode) != 0o600
        or parent.st_uid != os.getuid() or entry.st_uid != os.getuid() or entry.st_nlink != 1):
        raise ProgressUnavailable()
    for suffix in ('-wal', '-shm'):
        if os.path.lexists(str(path) + suffix):
            raise ProgressUnavailable()
    # Refuse WAL-format databases before opening SQLite; opening such a database
    # in read-only mode can still create a WAL/SHM sidecar in a writable folder.
    with path.open('rb') as db:
        header = db.read(20)
    if not header.startswith(b'SQLite format 3\0') or header[18:20] != b'\x01\x01':
        raise ProgressUnavailable()
    return entry.st_dev, entry.st_ino


def read_progress(request_id, path=WORKFLOW_DB):
    if not REQUEST_ID.fullmatch(request_id):
        raise ValueError('INVALID_REQUEST_ID')
    try:
        identity = _identity(path)
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=1, isolation_level=None)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('PRAGMA trusted_schema=OFF')
            db.execute('BEGIN')
            run = db.execute('SELECT state FROM workflow_runs WHERE official_request_id=?', (request_id,)).fetchone()
            if run is None:
                payload = {'workflowState': None, 'tasks': []}
            else:
                if run[0] not in RUN_STATES:
                    raise ProgressUnavailable()
                rows = db.execute('''
                    SELECT t.task_key,t.task_type,t.worker_type,t.status,p.task_key,t.updated_at,
                           t.depends_on_task_id IS NOT NULL
                    FROM workflow_tasks AS t
                    LEFT JOIN workflow_tasks AS p ON p.task_id=t.depends_on_task_id
                         AND p.official_request_id=t.official_request_id
                    WHERE t.official_request_id=?
                    ORDER BY t.rowid
                    LIMIT ?
                ''', (request_id, MAX_TASKS + 1)).fetchall()
                if len(rows) > MAX_TASKS:
                    raise ProgressUnavailable()
                tasks = []
                for key, task_type, worker, status, depends, updated_at, has_dependency in rows:
                    if (any(not isinstance(v, str) or not TASK_LABEL.fullmatch(v) for v in (key, task_type))
                        or (has_dependency and (not isinstance(depends, str) or not TASK_LABEL.fullmatch(depends)))
                        or worker not in WORKERS or status not in TASK_STATES or type(updated_at) is not int
                        or not 0 <= updated_at <= 2**53-1):
                        raise ProgressUnavailable()
                    tasks.append({'key':key, 'type':task_type, 'workerType':worker,
                                  'status':status, 'dependsOn':depends, 'updatedAt':updated_at})
                payload = {'workflowState':run[0], 'tasks':tasks}
            if _identity(path) != identity:
                raise ProgressUnavailable()
            return payload
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        raise ProgressUnavailable() from exc


MAX_ALARMS = 200


def read_alarms(path=WORKFLOW_DB):
    """Project only exhausted failures. Older ledgers may lack retry_count."""
    try:
        identity = _identity(path)
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=1, isolation_level=None)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('PRAGMA trusted_schema=OFF')
            db.execute('BEGIN')
            names={r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name IN ('workflow_tasks','workflow_runs')")}
            fields={r[1] for r in db.execute('PRAGMA table_info(workflow_tasks)')}
            if names!={'workflow_tasks','workflow_runs'} or not {
                'task_id','official_request_id','task_key','task_type','worker_type','status','updated_at'
            } <= fields:
                raise ProgressUnavailable()
            # Keep missing-column migration compatibility distinct from a broken
            # ledger or any other read error. Validate the parent lookup even then.
            db.execute('SELECT official_request_id FROM workflow_runs LIMIT 0')
            result=[]
            if 'retry_count' in fields:
                rows=db.execute('''
                    SELECT t.official_request_id,t.task_key,t.task_type,t.worker_type,t.retry_count,t.updated_at,
                           EXISTS(SELECT 1 FROM workflow_runs r WHERE r.official_request_id=t.official_request_id)
                    FROM workflow_tasks t
                    WHERE t.status='failed' AND t.retry_count >= 3
                    ORDER BY t.updated_at DESC,t.rowid DESC
                    LIMIT ?
                ''',(MAX_ALARMS+1,)).fetchall()
                for request_id,key,kind,worker,retry_count,updated_at,has_request in rows:
                    if (not isinstance(request_id,str) or not REQUEST_ID.fullmatch(request_id)
                        or any(not isinstance(v,str) or not TASK_LABEL.fullmatch(v) for v in (key,kind))
                        or worker not in WORKERS or type(retry_count) is not int or not 3<=retry_count<=2**53-1
                        or type(updated_at) is not int or not 0<=updated_at<=2**53-1):
                        raise ProgressUnavailable()
                    result.append({'officialRequestId':request_id,'key':key,'type':kind,'workerType':worker,
                                   'retryCount':retry_count,'updatedAt':updated_at,'hasRequest':bool(has_request)})
            if _identity(path)!=identity:
                raise ProgressUnavailable()
            return {'alarms':result[:MAX_ALARMS],'hasMore':len(result)>MAX_ALARMS}
    except (OSError,RuntimeError,sqlite3.Error) as exc:
        raise ProgressUnavailable() from exc
