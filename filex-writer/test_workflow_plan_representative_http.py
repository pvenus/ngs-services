from pathlib import Path
from urllib.request import urlopen
from urllib.error import HTTPError
import json
import sqlite3
import unittest

import test_workflow_preset_claim_http as preset_fixture
from test_workflow_delete_http import digest, encoded, handler
from project_data_service.concept_filex import FilexConcept
from project_data_service.filex_npc_authority import FilexCurrentNPC
from project_data_service.filex_npc_store import FilexNPCStore
from project_data_service.pds_owned_transport import OwnedNPCTransport
from project_data_service.workflow_plan_representative import PATH, WRITE_SCHEMA, ARTIFACT, RepresentativePlan, CONCEPT_FIELDS
from project_data_service.workflow_preset import WorkflowPreset, PATH as PRESET_PATH


class FixtureConcept(FilexConcept):
    def current(self):
        sections=[{'id':sid,'title':sid,'summary':{'sentence':sid},
                   'fields':[{'id':fid,'label':fid,'value':'trusted master '+fid} for fid in names]
                    +[{'id':'unrelated','label':'unrelated','value':'NOT PROMPT'}]}
                  for sid,names in CONCEPT_FIELDS.items()]
        return {'revision':58,'document':{'projectId':'ProjectBS','sections':sections}}


class Tests(unittest.TestCase):
    start=preset_fixture.Tests.start
    post=preset_fixture.Tests.post
    approve=preset_fixture.Tests.approve
    preset=preset_fixture.Tests.preset
    plan=preset_fixture.Tests.plan

    def start_new_writer(self,*,workflow_portrait=False,workflow_shared_plan=False,workflow_npc_planning_update=False,workflow_npc_folder_delete=False,workflow_npc_planning_markdown=False,workflow_npc_planning_add=False):
        _,self.preset_writer_url=self.start(handler(self.writer,self.original_token,workflow_delete=True,
                                                    workflow_preset=True,workflow_representative=True,workflow_portrait=workflow_portrait,
                                                    workflow_shared_plan=workflow_shared_plan,workflow_npc_planning_update=workflow_npc_planning_update,
                                                    workflow_npc_folder_delete=workflow_npc_folder_delete,workflow_npc_planning_markdown=workflow_npc_planning_markdown,
                                                    workflow_npc_planning_add=workflow_npc_planning_add))
        cfg={'schemaVersion':'pds-filex-cas.v1','projectId':'binari','scope':'npc','enabled':True,
             'endpoint':self.preset_writer_url,'tokenFile':str(Path(self.writer.root).parent/'writer-token')}
        source=FilexCurrentNPC(FilexNPCStore(lambda:OwnedNPCTransport(cfg)))
        self.pds.workflow_preset=WorkflowPreset(source,lambda c: self.pb if self.running and c==self.claim else None)

    def setUp(self):
        preset_fixture.Tests.setUp(self)
        self.plan()
        self.assertEqual(self.preset()[0],200)
        self.preset_id=self.c['taskId']
        self.c['taskId']='workflow.task.'+digest([self.c['officialRequestId'],'planning_representative'])
        self.c['claimStamp']=3
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE workflow_tasks SET status='completed',result_ref=? WHERE task_id=?",
                       (self.preset_id,self.preset_id))
            db.execute("""INSERT INTO workflow_tasks VALUES(?,?,'planning_representative','npc.plan-representative',
                       'document',?,NULL,3,?,'running')""",
                       (self.c['taskId'],self.c['officialRequestId'],encoded({'npcId':self.c['npcId']}).decode(),self.preset_id))
        s=self.pds.workflow_preset
        self.pds.workflow_plan_representative=RepresentativePlan(s.source,s.verify,FixtureConcept())

    def read(self,claim=None):
        return self.post({'claim':self.c if claim is None else claim},path=PATH+'/read')

    def publish(self,basis,artifact=None):
        if artifact is None:
            artifact={'schemaVersion':ARTIFACT,'taskId':self.c['taskId'],'projectId':'binari','npcId':'npc.fresh',
                      'categoryId':basis['categoryId'],'content':'새 대표 포즈 기획'}
        return self.post({'claim':self.c,'basis':basis,'artifact':artifact},path=PATH)

    def test_exact_claim_read_selected_context_and_single_category_commit(self):
        before=self.writer.owner_files('npc.fresh'); other=self.writer.owner_files('npc.other')
        olddb=self.db.read_bytes()
        with urlopen(self.base+PATH,timeout=3) as response:
            self.assertEqual(json.load(response),{'enabled':True,'schemaVersion':WRITE_SCHEMA})
        status,read=self.read();self.assertEqual(status,200)
        self.assertEqual(set(read),{'basis','character','conceptContext','presetContext'})
        self.assertEqual([x['id'] for x in json.loads(read['conceptContext'])['sections']],['character','expression'])
        self.assertNotIn('NOT PROMPT',read['conceptContext'])
        self.assertEqual(json.loads(read['presetContext'])['category']['content'],'')
        b=read['basis']
        from unittest.mock import patch
        with patch.object(self.writer,'admit',side_effect=AssertionError()), \
             patch.object(self.writer,'prepare_lifecycle',side_effect=AssertionError()), \
             patch.object(self.writer,'receipt_path',side_effect=AssertionError('no receipt lookup or persistence')):
            code,r=self.publish(b);self.assertEqual((code,r),(200,{'ok':True}))
            self.assertEqual(self.publish(b)[0],409) # stale optimistic CAS only
        self.assertFalse(self.writer.receipt_path(self.c['taskId']).exists())
        after=self.writer.owner_files('npc.fresh')
        category='npc.fresh/planning/'+b['categoryId']+'.json'
        self.assertEqual(set(before),set(after))
        self.assertEqual({k for k in before if before[k]!=after[k]}, {category,'npc.fresh/.owned-assets.json'})
        self.assertEqual(json.loads(after[category])['content'],'새 대표 포즈 기획')
        self.assertEqual(self.writer.owner_files('npc.other'),other)
        self.assertEqual(self.db.read_bytes(),olddb)
        self.assertEqual(self.writer.generation('npc.fresh')['activeLeases'],0)

    def test_deny_wrong_claim_changed_source_or_sibling(self):
        self.assertEqual(self.read({**self.c,'claimStamp':4})[0],403)
        self.assertEqual(self.read({**self.c,'npcId':'npc.other'})[0],403)
        code,b=self.read();self.assertEqual(code,200);b=b['basis']
        self.assertEqual(self.publish({**b,'revision':b['revision']+1})[0],409)
        self.assertEqual(self.publish({**b,'categoryId':'category.preset.'+'0'*32})[0],409)
        with sqlite3.connect(self.db) as db:db.execute("UPDATE workflow_tasks SET status='pending' WHERE task_id=?",(self.c['taskId'],))
        self.assertEqual(self.publish(b)[0],403)
        self.assertFalse(self.writer.receipt_path(self.c['taskId']).exists())


    def test_folder_snapshot_basis_skips_concept_and_keeps_legacy_and_owned_cas(self):
        from unittest.mock import patch
        code,legacy=self.read()
        self.assertEqual(code,200)
        folder={'claim':self.c,'inputMode':'folder-snapshot'}
        service=self.pds.workflow_plan_representative
        with patch.object(service.concept,'current',side_effect=ValueError('fixture missing concept fields')) as projection:
            with urlopen(self.base+PATH+'?inputMode=folder-snapshot',timeout=3) as response:
                self.assertEqual(json.load(response),{'enabled':True,'schemaVersion':WRITE_SCHEMA})
            code,basis=self.post(folder,path=PATH+'/read')
            self.assertEqual(code,200)
            self.assertEqual(basis,{k:v for k,v in legacy.items() if k!='conceptContext'})
            self.assertEqual(self.post({**folder,'claim':{**self.c,'claimStamp':4}},path=PATH+'/read')[0],403)
            self.assertEqual(self.post({**folder,'claim':{**self.c,'npcId':'npc.other'}},path=PATH+'/read')[0],403)
            self.assertEqual(self.post({**folder,'inputMode':'unsupported'},path=PATH+'/read')[0],409)
            self.assertEqual(self.post({**folder,'extra':True},path=PATH+'/read')[0],409)
            self.assertEqual(self.post(folder,path=PATH+'/read?inputMode=folder-snapshot')[0],403)
            self.assertEqual(self.post(folder,path=PATH+'?inputMode=folder-snapshot')[0],403)
            for query in ('?inputMode=unsupported','?inputMode=folder-snapshot&inputMode=folder-snapshot'):
                with self.assertRaises(HTTPError) as failure:
                    urlopen(self.base+PATH+query,timeout=3)
                self.assertEqual(failure.exception.code,403)
                failure.exception.close()
            projection.assert_not_called()
            self.assertEqual(self.read()[0],409)
            with self.assertRaises(HTTPError) as failure:
                urlopen(self.base+PATH,timeout=3)
            self.assertEqual(failure.exception.code,409)
            failure.exception.close()
            self.assertEqual(projection.call_count,2)
            p=basis['basis']
            self.assertEqual(self.publish({**p,'revision':p['revision']+1})[0],409)
            self.assertEqual(self.publish(p)[0],200)
            self.assertEqual(projection.call_count,2)
            code,fresh=self.post(folder,path=PATH+'/read')
            self.assertEqual(code,200)
            self.assertEqual(json.loads(fresh['presetContext'])['category']['content'],'')
            self.assertEqual(projection.call_count,2)

    def test_ordinary_claim_overwrites_current_content_ignores_and_preserves_old_history(self):
        code,view=self.read();self.assertEqual(code,200)
        original=view['basis'];task_id=self.c['taskId']
        # Simulate pre-upgrade artifacts; malformed historical content cannot
        # affect this operation, and the unrelated preset receipt is never read.
        rp=self.writer.receipt_path(task_id);self.writer.write(rp,b'{"oldRepresentative":true}')
        old_receipt=rp.read_bytes()
        preset_receipt=self.writer.receipt_path(self.preset_id)
        self.assertTrue(preset_receipt.is_file())
        preset_receipt.rename(Path(self.writer.root).parent/'fixture-preset-history.json')
        self.assertFalse(preset_receipt.exists())
        with self.subTest('legacy retry fields rejected'):
            self.assertEqual(self.post({'claim':self.c,'inputMode':'folder-snapshot','retry':True},path=PATH+'/read')[0],409)
        self.assertEqual(self.publish(original),(200,{'ok':True}))
        unrelated=self.writer.owner_files('npc.other')
        cid=original['categoryId']
        # A fresh ordinary running claim suffices; no clear/recovery flags.
        self.c['claimStamp']=8
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE workflow_tasks SET updated_at=8 WHERE task_id=?",(task_id,))
        from unittest.mock import patch
        with patch.object(self.writer,'receipt_path',side_effect=AssertionError('no receipt lookup or persistence')):
            read_code,current=self.read();self.assertEqual(read_code,200)
            self.assertEqual(current['basis']['categoryId'],cid)
            self.assertEqual(json.loads(current['presetContext'])['category']['content'],'')
            self.assertNotIn('새 대표 포즈 기획',current['presetContext'])
            basis=current['basis']
            a={'schemaVersion':ARTIFACT,'taskId':task_id,'projectId':'binari','npcId':'npc.fresh',
               'categoryId':cid,'content':'새 클레임의 결과'}
            submit={'claim':self.c,'basis':basis,'artifact':a}
            self.assertEqual(self.post({**submit,'retry':True},path=PATH)[0],409)
            self.assertEqual(self.post({**submit,'claim':{**self.c,'claimStamp':7}},path=PATH)[0],403)
            self.assertEqual(self.publish(original,a)[0],409)
            self.assertEqual(self.publish(basis,a),(200,{'ok':True}))
        self.assertEqual(rp.read_bytes(),old_receipt)
        self.assertEqual(self.writer.owner_files('npc.other'),unrelated)
        self.assertEqual(json.loads(self.writer.owner_files('npc.fresh')['npc.fresh/planning/'+cid+'.json'])['content'],a['content'])
        self.assertFalse(any(self.writer.safe('.requests').glob('workflow.attempt.*')))

    def test_owned_write_rolls_forward_without_receipt_after_interruption(self):
        from unittest.mock import patch
        _,view=self.read();b=view['basis']
        recover=self.writer.recover_owned;journal=self.writer.safe('.owned-tx.json')
        def interrupt():
            if journal.is_file():
                raise OSError('fixture interrupt before journal application')
            return recover()
        with patch.object(self.writer,'recover_owned',side_effect=interrupt):
            self.assertNotEqual(self.publish(b)[0],200)
        record=json.loads(journal.read_bytes())
        self.assertIsNone(record['requestId']);self.assertIsNone(record['receipt'])
        self.assertEqual(record['removes'],[])
        # An ordinary reader runs Filex recovery under its lock.
        status,view=self.read();self.assertEqual(status,200)
        self.assertEqual(view['basis']['revision'],b['revision']+1)
        self.assertFalse(journal.exists())
        category=self.writer.safe('npc.fresh/planning/'+b['categoryId']+'.json')
        self.assertEqual(json.loads(category.read_bytes())['content'],'새 대표 포즈 기획')
        self.assertFalse(self.writer.receipt_path(self.c['taskId']).exists())
