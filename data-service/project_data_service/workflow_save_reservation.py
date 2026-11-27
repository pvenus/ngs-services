"""A short, exact SQLite reservation for a document publication."""
from contextlib import closing
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
        return {'taskId':claim['taskId'],'claimStamp':claim['claimStamp']}

    def finish(self):
        claim=self.claim
        require(self._change("""UPDATE workflow_tasks SET save_started_at=NULL
            WHERE task_id=? AND official_request_id=? AND updated_at=? AND worker_type='document'
            AND status='running' AND result_ref IS NULL AND save_started_at=?""",
            (claim['taskId'],claim['officialRequestId'],claim['claimStamp'],self.started))==1)

def publish_reserved(reservation, source, operation):
    # The first Filex call after reserving is outside every SQLite transaction.
    # Keep the old claim pinned on an uncertain error until Filex confirms its fence.
    with source.store.factory() as writer:
        if writer.capabilities().get('workflowDocumentSaveFenceV1') is not True:
            raise OSError('DOCUMENT_SAVE_FENCE_UNAVAILABLE')
    fence=reservation.enter()
    try:
        result=operation(fence)
    except Exception:
        try:
            with source.store.factory() as writer:
                if writer.revoke_workflow_document_save(fence)!={'ok':True}:
                    raise OSError('DOCUMENT_SAVE_FENCE_UNCONFIRMED')
        except Exception as error:
            raise OSError('DOCUMENT_SAVE_FENCE_UNCONFIRMED') from error
        reservation.finish()
        # Only a completed writer-side fence may allow ordinary retry.
        raise ValueError('DOCUMENT_SAVE_FENCED_FAILURE') from None
    # The worker clears the reservation in the same transaction that records completion.
    return result

DOCUMENT_SAVE_TIMEOUT_MS=180_000

def expire_document_saves(verifier, source, *, now_ms=None):
    """Ordinary retry only after an authoritative Filex rejection of old writes."""
    now=int(time.time()*1000) if now_ms is None else now_ms
    with verifier.snapshot() as db:
        if not any(row['name']=='save_started_at' for row in db.execute('PRAGMA table_info(workflow_tasks)')):
            return []
        pending=[dict(r) for r in db.execute("""SELECT task_id,official_request_id,updated_at,save_started_at
            FROM workflow_tasks WHERE worker_type='document' AND status='running' AND result_ref IS NULL
              AND save_started_at<=? ORDER BY save_started_at LIMIT 10""",(now-DOCUMENT_SAVE_TIMEOUT_MS,))]
    released=[]
    for row in pending:
        fence={'taskId':row['task_id'],'claimStamp':row['updated_at']}
        if len(fence['taskId'])!=78 or not fence['taskId'].startswith('workflow.task.') or any(c not in '0123456789abcdef' for c in fence['taskId'][14:]):
            continue
        try:
            with source.store.factory() as writer:
                if writer.revoke_workflow_document_save(fence)!={'ok':True}:continue
            changed=SaveReservation(verifier,None)._change("""UPDATE workflow_tasks
                SET save_started_at=NULL,status='failed',updated_at=?
                WHERE task_id=? AND official_request_id=? AND updated_at=? AND save_started_at=?
                 AND worker_type='document' AND status='running' AND result_ref IS NULL""",
                (max(now,row['updated_at']+1),row['task_id'],row['official_request_id'],row['updated_at'],row['save_started_at']))
            if changed:released.append(row['task_id'])
        except (OSError,ValueError,sqlite3.Error):
            # Retry the fence later. Do not advertise a failure without proof.
            continue
    return released
