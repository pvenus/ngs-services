"""Exercise the actual planned-claim PDS -> Filex whole-NPC transaction."""
import copy
import json
import sqlite3

from test_workflow_native_media_http import NativeFixture
from test_workflow_delete_http import digest,encoded
from project_data_service.single_target_delete import SingleTargetDelete,PATH,NPC_STANDALONE_CAPABILITY
from project_data_service.workflow_delete import WorkflowDelete,NPC_ROOT


class PinnedNpcDelete(NativeFixture):
    def configure(self):
        media=self.pds.workflow_native_media
        self.pds.workflow_delete=WorkflowDelete(media.source,media.verifier)
        legacy=self.pds.workflow_delete.storage_provider()
        self.pds.single_target_delete=SingleTargetDelete({NPC_ROOT:self.pds.workflow_delete.with_standalone_storage(legacy)})
        self.pds.npc_standalone_delete=True

    def delete_claim(self,label,owner,creation):
        oid='request.official.'+label*64
        tid='workflow.task.'+digest([oid,'file_delete'])
        target={'root':NPC_ROOT,'path':owner,'kind':'directory'}
        inp={'target':target,'expected':{'creationTaskId':creation}}
        schema='ngs.file-delete.v1';payload={**inp,'schemaVersion':schema,'projectId':'binari'}
        with sqlite3.connect(self.db) as db:
            db.execute("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,1,'planned',1,NULL)",
                       (oid,'binari','file.delete',schema,encoded(payload).decode(),digest(payload)))
            db.execute("INSERT INTO workflow_tasks VALUES(?,?,'file_delete','file.delete','file',?,NULL,20,NULL,'running')",
                       (tid,oid,encoded(inp).decode()))
        return {'target':target,'claim':{'officialRequestId':oid,'taskId':tid,'projectId':'binari',
                                         'payloadSha256':digest(payload),'claimStamp':20}}

    def test_exact_creation_receipt_is_required_and_full_folder_is_recoverable(self):
        self.configure()
        owner=self.c['npcId']; other='npc.other'
        before=self.writer.owner_files(owner); neighbors=self.writer.owner_files(other)
        self.assertGreater(len(before),3)
        creation=json.loads(before[owner+'/metadata.json'])['creationBasis']['requestId']
        bad=self.delete_claim('8',owner,'workflow.task.'+'9'*64)
        self.assertEqual(self.post(bad,path=PATH),(409,{'error':'STORAGE_DELETE_STALE_TARGET'}))
        wrong_owner=self.delete_claim('a',other,creation)
        self.assertEqual(self.post(wrong_owner,path=PATH),(409,{'error':'STORAGE_DELETE_STALE_TARGET'}))
        self.assertEqual(before,self.writer.owner_files(owner))
        claim=self.delete_claim('b',owner,creation)
        ok,result=self.post(claim,path=PATH)
        self.assertEqual(ok,200,result)
        self.assertTrue(result['targetAbsent'])
        self.assertEqual(self.writer.owner_files(owner),{})
        self.assertEqual(self.writer.owner_files(other),neighbors)
        self.assertNotIn(owner,self.writer.snapshot()['documents']['metadata.json']['characterOrder'])
        self.assertEqual(self.post(claim,path=PATH),(200,result))

        # Publish a new preset with the same requested NPC ID through the
        # existing authenticated writer. The old queued intent must not move it.
        preset=self.pds.workflow_preset
        snap,marker,_=preset.source._read()
        next_task='workflow.task.'+'c'*64
        artifact=copy.deepcopy(self.art);artifact['taskId']=next_task
        binding={'officialRequestId':'request.official.'+'c'*64,
                 'taskId':next_task,'projectId':'binari','npcId':owner,'payloadSha256':'c'*64,
                 'artifactSha256':digest(artifact),'expectedRevision':marker['revision'],
                 'expectedSourceVersion':snap['sourceVersion'],'predecessorReceiptRef':None}
        preset._commit(binding,artifact)
        replacement=self.writer.owner_files(owner)
        self.assertEqual(json.loads(replacement[owner+'/metadata.json'])['creationBasis']['requestId'],next_task)
        stale=self.delete_claim('f',owner,creation)
        self.assertEqual(self.post(stale,path=PATH),(409,{'error':'STORAGE_DELETE_STALE_TARGET'}))
        self.assertEqual(self.writer.owner_files(owner),replacement)
        self.assertIn(owner,self.writer.snapshot()['documents']['metadata.json']['characterOrder'])

