"""A short, exact SQLite reservation for a document publication."""
from contextlib import closing
from http.client import HTTPException
import sqlite3
import time

from .workflow_delete_claim import require

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

    def enter(self):
        claim=self.claim
        start=max(int(time.time()*1000),claim['claimStamp'])
        n=self._change("""UPDATE workflow_tasks SET save_started_at=?
            WHERE task_id=? AND official_request_id=? AND updated_at=? AND worker_type='document'
            AND status='running' AND result_ref IS NULL AND save_started_at IS NULL
            AND EXISTS(SELECT 1 FROM workflow_runs r WHERE r.official_request_id=workflow_tasks.official_request_id
                AND r.state='planned' AND r.payload_sha256=? AND r.reason IS NULL)""",
            (start,claim['taskId'],claim['officialRequestId'],claim['claimStamp'],claim['payloadSha256']))
        require(n==1)
        self.started=start

    def finish(self):
        claim=self.claim
        require(self._change("""UPDATE workflow_tasks SET save_started_at=NULL
            WHERE task_id=? AND official_request_id=? AND updated_at=? AND worker_type='document'
            AND status='running' AND result_ref IS NULL AND save_started_at=?""",
            (claim['taskId'],claim['officialRequestId'],claim['claimStamp'],self.started))==1)

def publish_reserved(reservation, operation):
    # Only PDS owns workflow state. Never hold the SQLite transaction over Filex I/O.
    reservation.enter()
    try:
        return operation()
    except Exception as error:
        reservation.finish()
        if isinstance(error,(OSError,HTTPException)):
            raise ValueError('DOCUMENT_SAVE_IO_FAILED') from error
        raise
    # On success the worker records completion and clears the marker atomically.

DOCUMENT_SAVE_TIMEOUT_MS=180_000

def expire_document_saves(verifier, *, now_ms=None):
    """Fail abandoned saves; no Filex reconciliation or replay."""
    now=int(time.time()*1000) if now_ms is None else now_ms
    with verifier.snapshot() as db:
        if not any(row['name']=='save_started_at' for row in db.execute('PRAGMA table_info(workflow_tasks)')):
            return []
        pending=[dict(r) for r in db.execute("""SELECT task_id,official_request_id,updated_at,save_started_at
            FROM workflow_tasks WHERE worker_type='document' AND status='running' AND result_ref IS NULL
              AND save_started_at<=? ORDER BY save_started_at LIMIT 10""",(now-DOCUMENT_SAVE_TIMEOUT_MS,))]
    released=[]
    for row in pending:
        try:
            changed=SaveReservation(verifier,None)._change("""UPDATE workflow_tasks
                SET save_started_at=NULL,status='failed',updated_at=?
                WHERE task_id=? AND official_request_id=? AND updated_at=? AND save_started_at=?
                 AND worker_type='document' AND status='running' AND result_ref IS NULL""",
                (max(now,row['updated_at']+1),row['task_id'],row['official_request_id'],row['updated_at'],row['save_started_at']))
            if changed:released.append(row['task_id'])
        except (OSError,ValueError,sqlite3.Error):
            # A busy DB can be checked again on the next sweep.
            continue
    return released
