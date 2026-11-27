import sqlite3
import unittest

from project_data_service.workflow_db_boundary import require_closed_snapshot, workflow_snapshot_open
from project_data_service.workflow_save_reservation import expire_document_saves, save_handoff
from test_workflow_portrait_representative_http import Tests as Fixture

class Boundary(unittest.TestCase):
    def test_filex_transport_rejects_workflow_snapshot_before_network(self):
        from project_data_service.filex_cas_transport import FilexCASTransport
        token=workflow_snapshot_open.set(True)
        try:
            with self.assertRaisesRegex(ValueError,'FILEX_CALL_WITH_WORKFLOW_SNAPSHOT'):
                FilexCASTransport.call(None,'POST','/v1/workflow/plan-current')
        finally:
            workflow_snapshot_open.reset(token)
        require_closed_snapshot()

class Lifecycle(Fixture):
    def test_multipart_lost_reply_matches_attempt_and_expires_without_replay(self):
        from time import time
        svc=self.pds.workflow_portrait_representative
        svc.verifier.file_save_handoff=True
        with sqlite3.connect(self.db) as db:
            db.execute('ALTER TABLE workflow_tasks ADD COLUMN save_started_at INTEGER')
        with save_handoff(svc.verifier,self.c,continuation=True):
            pass
        with save_handoff(svc.verifier,self.c,continuation=True):
            pass
        with sqlite3.connect(self.db) as db:
            state=db.execute('SELECT status,save_started_at FROM workflow_tasks WHERE task_id=?',(self.c['taskId'],)).fetchone()
        self.assertEqual(state[0],'running')
        self.assertIsNotNone(state[1])
        self.assertEqual(expire_document_saves(svc.verifier,now_ms=int(time()*1000)+181000),[self.c['taskId']])
        self.assertEqual(self.read()[0],403)
