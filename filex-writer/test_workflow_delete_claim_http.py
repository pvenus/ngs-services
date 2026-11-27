from pathlib import Path
import os
import sqlite3
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import unittest

from test_workflow_delete_http import Tests as DeleteFixture, digest, encoded
from project_data_service.workflow_delete import WorkflowDelete, PATH
from project_data_service.workflow_delete_claim import ClaimDenied, PrivatePlannerClaim


class Tests(DeleteFixture):
    def setUp(self, owner='npc.test', picks=None):
        super().setUp(owner)
        root=self.writer.root.parent/'planner';root.mkdir(mode=0o700)
        self.db=root/'workflow.sqlite'
        fd=os.open(self.db,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        official='request.official.'+'d'*64
        task='workflow.task.'+digest([official,'delete'])
        schema=('ngs.npc-one-stop.selection.replace.v1' if picks else 'ngs.npc-one-stop.replace.v1')
        payload={'schemaVersion':schema,'projectId':'binari','intent':'exact test deletion',
                 'character':{'npcId':owner,'name':'테스트 인물','slug':owner.split('.',1)[1] if owner.startswith('character.') else 'fixture','role':'마을 주민',
                              'actId':'act01','chapterId':'chapter01'},'sourceRefs':['fixture'],
                 **({'motionSelection':picks} if picks else {'includeCombat':False})}
        self.c={'officialRequestId':official,'taskId':task,'projectId':'binari','npcId':owner,
                'payloadSha256':digest(payload),'claimStamp':2}
        with sqlite3.connect(self.db) as db:
            db.executescript('''CREATE TABLE workflow_runs(official_request_id,project_id,request_type,schema_version,
              payload,payload_sha256,registered_at,state,updated_at,reason);
              CREATE TABLE workflow_tasks(task_id,official_request_id,task_key,task_type,worker_type,task_input,
              result_ref,updated_at,depends_on_task_id,status);''')
            db.execute("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,1,'planned',1,NULL)",
                       (official,'binari','npc.workflow',schema,encoded(payload).decode(),digest(payload)))
            inp={'npcId':owner,'scope':'exact-npc-folder','preserveRequestHistory':True,'noOldDataReuse':True}
            db.execute("INSERT INTO workflow_tasks VALUES(?,?,'delete','npc.delete-folder','file',?,NULL,2,NULL,'running')",
                       (task,official,encoded(inp).decode()))
        st=self.db.stat()
        self.verifier=PrivatePlannerClaim(str(self.db),st.st_dev,st.st_ino)
        self.pds.workflow_delete=WorkflowDelete(self.source,self.verifier)

    def production(self,claim=None,headers=None):
        return self.post({'claim':self.c if claim is None else claim},headers=headers)

    def test_private_readonly_live_claim_then_verified_delete_and_replay(self):
        req=Request(self.base+PATH)
        with urlopen(req,timeout=3) as r:
            self.assertEqual(r.code,200)
        before=self.db.read_bytes()
        status,receipt=self.production()
        self.assertEqual(status,200)
        self.assertEqual(self.production(),(200,receipt))
        self.assertEqual(receipt['receiptId'],self.c['taskId'])
        self.assertTrue(receipt['verified'] and receipt['targetAbsent'])
        self.assertFalse((self.writer.root/'npc.test').exists())
        self.assertEqual(self.writer.generation('npc.other')['activeLeases'],1)
        raw=self.writer.receipt(self.c['taskId'])
        self.assertEqual(self.writer.verified_restorable_backup(raw['backupId'])[0]['characterId'],'npc.test')
        self.assertEqual(self.db.read_bytes(),before)

    def test_private_rejects_pending_forged_stamp_actor_scope_and_caller_cas(self):
        for change in ({'claimStamp':3},{'npcId':'npc.other'},{'expectedRevision':1},{'projectId':'outside'}):
            self.assertEqual(self.production({**self.c,**change})[0],403)
        self.assertEqual(self.production(headers={'Origin':'http://127.0.0.1'})[0],403)
        self.assertEqual(self.post({'claim':self.c,'binding':self.b})[0],409)
        with sqlite3.connect(self.db) as db:db.execute("UPDATE workflow_tasks SET status='pending'")
        self.assertEqual(self.production()[0],403)
        self.assertFalse(self.writer.receipt_path(self.c['taskId']).exists())
        self.assertTrue(self.writer.owner_files('npc.test'))

    def test_proof_releases_snapshot_before_handoff_and_denies_stale_stamp(self):
        peer=sqlite3.connect(self.db,timeout=.05)
        try:
            with self.verifier.authorized(self.c):
                peer.execute("UPDATE workflow_tasks SET updated_at=3")
                peer.commit()
            self.assertEqual(self.production()[0],403)
        finally:peer.close()

    def test_wrong_inode_and_wal_refuse(self):
        st=self.db.stat()
        with self.assertRaises(ClaimDenied):
            PrivatePlannerClaim(str(self.db),st.st_dev,st.st_ino+1)
        peer=sqlite3.connect(self.db)
        try:
            peer.execute('PRAGMA journal_mode=WAL').fetchone()
            self.assertEqual(self.production()[0],403)
        finally:peer.close()
