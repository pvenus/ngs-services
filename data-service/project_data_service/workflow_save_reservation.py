"""A short, exact SQLite reservation for a workflow file publication."""
from contextlib import closing, contextmanager, nullcontext
from http.client import HTTPException
import logging
import sqlite3
import threading
import time

from .workflow_delete_claim import require

LOG=logging.getLogger(__name__)
_ACTIVE=set()
_ACTIVE_LOCK=threading.Lock()

def _try_enter(task):
    with _ACTIVE_LOCK:
        if task in _ACTIVE:return False
        _ACTIVE.add(task)
        return True

def _leave(task):
    with _ACTIVE_LOCK:
        _ACTIVE.remove(task)

class SaveReservation:
    def __init__(self, verifier, claim):
        self.verifier=verifier
        self.claim=claim
        self.started=None

    def _change(self, sql, args):
        self.verifier.check_path()
        with closing(sqlite3.connect(self.verifier.path.as_uri()+'?mode=rw', uri=True, timeout=5, isolation_level=None)) as db:
            db.execute('PRAGMA trusted_schema=OFF')
            db.execute('BEGIN IMMEDIATE')
            try:
                self.verifier.check_path()
                changed=db.execute(sql,args).rowcount
                db.execute('COMMIT')
                return changed
            except BaseException:
                if db.in_transaction:db.execute('ROLLBACK')
                raise

    def enter(self, *, document=True, continuation=False):
        claim=self.claim
        start=max(int(time.time()*1000),claim['claimStamp'])
        if not document:
            with self.verifier.snapshot() as db:
                if not any(row['name']=='save_started_at' for row in db.execute('PRAGMA table_info(workflow_tasks)')):
                    return
        n=self._change("""UPDATE workflow_tasks SET save_started_at=coalesce(save_started_at,?)
            WHERE task_id=? AND official_request_id=? AND updated_at=? AND (?=0 OR worker_type='document')
            AND status='running' AND result_ref IS NULL AND (?=1 OR save_started_at IS NULL)
            AND EXISTS(SELECT 1 FROM workflow_runs r WHERE r.official_request_id=workflow_tasks.official_request_id
                AND r.state='planned' AND r.project_id='binari' AND r.payload_sha256=? AND r.reason IS NULL)""",
            (start,claim['taskId'],claim['officialRequestId'],claim['claimStamp'],int(document),int(continuation),claim['payloadSha256']))
        require(n==1)
        self.started=start

    def complete(self, result_ref):
        """Finalize only the exact reserved save, after storage returns its receipt."""
        claim=self.claim
        require(self.started is not None and isinstance(result_ref,str) and bool(result_ref))
        require(self._change("""UPDATE workflow_tasks
            SET status='completed',result_ref=?,save_started_at=NULL,updated_at=?
            WHERE task_id=? AND official_request_id=? AND updated_at=? AND save_started_at=?
            AND status='running' AND result_ref IS NULL
            AND EXISTS(SELECT 1 FROM workflow_runs r WHERE r.official_request_id=workflow_tasks.official_request_id
                AND r.state='planned' AND r.project_id='binari' AND r.payload_sha256=? AND r.reason IS NULL)""",
            (result_ref,max(int(time.time()*1000),claim['claimStamp']+1),claim['taskId'],
             claim['officialRequestId'],claim['claimStamp'],self.started,claim['payloadSha256']))==1)

    def finish(self):
        claim=self.claim
        require(self._change("""UPDATE workflow_tasks SET save_started_at=NULL
            WHERE task_id=? AND official_request_id=? AND updated_at=? AND worker_type='document'
            AND status='running' AND result_ref IS NULL AND save_started_at=?""",
            (claim['taskId'],claim['officialRequestId'],claim['claimStamp'],self.started))==1)

def publish_reserved(reservation, operation):
    # Only PDS owns workflow state. Never hold the SQLite transaction over Filex I/O.
    if getattr(reservation.verifier,'file_save_handoff',False):
        try:
            with save_handoff(reservation.verifier,reservation.claim):
                return operation()
        except (OSError,HTTPException) as error:
            raise ValueError('DOCUMENT_SAVE_IO_FAILED') from error
    reservation.enter()
    try:
        return operation()
    except Exception as error:
        reservation.finish()
        if isinstance(error,(OSError,HTTPException)):
            raise ValueError('DOCUMENT_SAVE_IO_FAILED') from error
        raise
    # On success the worker records completion and clears the marker atomically.

@contextmanager
def save_handoff(verifier, claim, *, continuation=False, require_reservation=False):
    """Exact, persistent save ownership after proof, with no SQLite over I/O.

    Continuation is only for the serial frame-upload protocol. The in-process
    guard prevents two Filex calls for the same task during the same upload.
    """
    if not getattr(verifier,'file_save_handoff',False) and not require_reservation:
        yield
        return
    task=claim['taskId']
    require(_try_enter(task))
    reservation=SaveReservation(verifier,claim)
    try:
        reservation.enter(document=False,continuation=continuation)
        try:
            require(not require_reservation or reservation.started is not None)
            yield reservation
        except Exception:
            if reservation.started is not None:
                try:
                    reservation._change("""UPDATE workflow_tasks SET save_started_at=NULL,status='failed',updated_at=?
                      WHERE task_id=? AND official_request_id=? AND updated_at=? AND status='running'
                      AND result_ref IS NULL AND save_started_at IS NOT NULL""",
                      (max(int(time.time()*1000),claim['claimStamp']+1),task,claim['officialRequestId'],claim['claimStamp']))
                except (OSError,sqlite3.Error,ValueError):
                    LOG.warning('save failure deferred to expiry for task %s',task)
            raise
    finally:
        _leave(task)

def optionally_save(verifier, claim, saving, *, continuation=False):
    return save_handoff(verifier,claim,continuation=continuation) if saving else nullcontext()


DOCUMENT_SAVE_TIMEOUT_MS=180_000

def expire_document_saves(verifier, *, now_ms=None):
    """Fail abandoned saves; no Filex reconciliation or replay."""
    now=int(time.time()*1000) if now_ms is None else now_ms
    with verifier.snapshot() as db:
        if not any(row['name']=='save_started_at' for row in db.execute('PRAGMA table_info(workflow_tasks)')):
            return []
        pending=[dict(r) for r in db.execute("""SELECT task_id,official_request_id,updated_at,save_started_at
            FROM workflow_tasks WHERE status='running' AND result_ref IS NULL
              AND save_started_at<=? ORDER BY save_started_at LIMIT 10""",(now-DOCUMENT_SAVE_TIMEOUT_MS,))]
    released=[]
    for row in pending:
        task=row['task_id']
        if not _try_enter(task):continue
        try:
            changed=SaveReservation(verifier,None)._change("""UPDATE workflow_tasks
                SET save_started_at=NULL,status='failed',updated_at=?
                WHERE task_id=? AND official_request_id=? AND updated_at=? AND save_started_at=?
                 AND status='running' AND result_ref IS NULL""",
                (max(now,row['updated_at']+1),row['task_id'],row['official_request_id'],row['updated_at'],row['save_started_at']))
            if changed:released.append(row['task_id'])
        except sqlite3.Error as error:
            code=getattr(error,'sqlite_errorcode',0) or 0
            LOG.warning('save expiry state write deferred reason=%s',
                        'SQLITE_BUSY' if code&255==sqlite3.SQLITE_BUSY else 'SQLITE_ERROR')
        except (OSError,ValueError):
            LOG.warning('save expiry state write deferred reason=WORKFLOW_DB_UNAVAILABLE')
        finally:
            _leave(task)
    return released
