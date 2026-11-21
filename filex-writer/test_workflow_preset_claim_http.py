import json
import os
from pathlib import Path
import sqlite3
import unittest
from urllib.request import Request,urlopen

import test_workflow_preset_http as fixture
from project_data_service.workflow_delete_claim import PrivatePlannerClaim
from project_data_service.workflow_preset import PATH, WorkflowPreset, REGISTRY_SHA, CATEGORIES


class Tests(unittest.TestCase):
    start=fixture.Tests.start
    post=fixture.Tests.post
    start_new_writer=fixture.Tests.start_new_writer
    approve=fixture.Tests.approve

    def setUp(self):
        fixture.Tests.setUp(self)
        root=self.writer.root.parent/'preset-planner';root.mkdir(mode=0o700)
        self.db=root/'workflow.sqlite'
        fd=os.open(self.db,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        with sqlite3.connect(self.db) as db:
            db.executescript('''CREATE TABLE workflow_runs(official_request_id,project_id,request_type,schema_version,
              payload,payload_sha256,registered_at,state,updated_at,reason);
              CREATE TABLE workflow_tasks(task_id,official_request_id,task_key,task_type,worker_type,task_input,
              result_ref,updated_at,depends_on_task_id,status);''')
        st=self.db.stat()
        self.pds.workflow_preset=WorkflowPreset(self.pds.workflow_preset.source,
                                               PrivatePlannerClaim(str(self.db),st.st_dev,st.st_ino))

    def plan(self,owner='npc.fresh',replace=False):
        official='request.official.'+'d'*64
        rid='workflow.task.'+fixture.digest([official,'preset'])
        parent='workflow.task.'+fixture.digest([official,'delete']) if replace else None
        schema='ngs.npc-one-stop.replace.v1' if replace else 'ngs.npc-one-stop.v1'
        payload={'schemaVersion':schema,'projectId':'binari','intent':'exact test preset',
                 'character':{**self.art['character'],'npcId':owner},'sourceRefs':['fixture'],'includeCombat':False}
        self.c={'officialRequestId':official,'taskId':rid,'projectId':'binari','npcId':owner,
                'payloadSha256':fixture.digest(payload),'claimStamp':2}
        inp={'npcId':owner,'mode':'fresh-replacement' if replace else 'new','noOldDataReuse':True}
        with sqlite3.connect(self.db) as db:
            db.execute("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,1,'planned',1,NULL)",
                       (official,'binari','npc.workflow',schema,fixture.encoded(payload).decode(),fixture.digest(payload)))
            db.execute("INSERT INTO workflow_tasks VALUES(?,?,'preset','npc.preset','document',?,NULL,2,?,'running')",
                       (rid,official,fixture.encoded(inp).decode(),parent))
        self.art['character']=payload['character'];self.art['taskId']=rid;self.art['presetRegistrySha256']=REGISTRY_SHA
        profile=self.art['profile']
        profile['planningCategories']=[{'id':'category.preset.'+fixture.digest(['npc.standard','3',rid,cid])[:32],
               'name':name,'content':'',**({'planningRole':role} if role else {})} for cid,name,role in CATEGORIES]
        profile['planningPreset']['visibleCategoryIds']=[x['id'] for x in profile['planningCategories']]

    def preset(self,claim=None,artifact=None,ref=None,headers=None):
        return self.post({'claim':self.c if claim is None else claim,'artifact':self.art if artifact is None else artifact,
                          'predecessorReceiptRef':ref},path=PATH,headers=headers)

    def test_ready_new_commit_replay_and_no_planner_writes(self):
        self.plan()
        if os.environ.get('WORKFLOW_JAVASCRIPT_PRESET_ARTIFACT'):
            self.art=json.loads(Path(os.environ['WORKFLOW_JAVASCRIPT_PRESET_ARTIFACT']).read_bytes())
        with urlopen(Request(self.base+PATH),timeout=3) as r:
            self.assertEqual(json.load(r),{'enabled':True,'schemaVersion':'workflow.preset-publication-receipt.v1'})
        before=self.db.read_bytes()
        code,receipt=self.preset()
        self.assertEqual(code,200);self.assertEqual(self.preset(),(200,receipt))
        self.assertEqual(receipt['artifactSha256'],fixture.digest(self.art))
        self.assertEqual(self.writer.generation('npc.fresh')['activeLeases'],0)
        self.assertEqual(self.db.read_bytes(),before)

    def test_replacement_storage_view_only_and_no_dependency_recheck(self):
        self.assertEqual(self.post()[0],200)
        self.plan('npc.test',replace=True)
        # No preceding planner task row exists; PDS must not independently inspect it.
        self.assertEqual(self.preset(ref='workflow.task.'+'f'*64)[0],409)
        self.assertEqual(self.preset()[0],200)
        self.assertEqual(self.writer.state('npc.test')['previousGenerationId'],self.g)

    def test_task_stamp_official_artifact_scope_and_cas_failure(self):
        self.plan('npc.test')
        self.assertEqual(self.preset({**self.c,'claimStamp':3})[0],403)
        self.assertEqual(self.preset({**self.c,'expectedRevision':1})[0],403)
        self.assertEqual(self.preset(headers={'Origin':'http://127.0.0.1'})[0],403)
        self.assertEqual(self.preset(artifact={**self.art,'character':{**self.art['character'],'role':'다른 역할'}})[0],409)
        self.assertEqual(self.preset(artifact={**self.art,'presetRegistrySha256':'f'*64})[0],409)
        self.assertEqual(self.preset()[0],409) # still active and present.
        self.assertTrue(self.writer.owner_files('npc.test'))
        self.assertFalse(self.writer.receipt_path(self.c['taskId']).exists())
        with sqlite3.connect(self.db) as db: db.execute("UPDATE workflow_tasks SET worker_type='file'")
        self.assertEqual(self.preset()[0],403)
