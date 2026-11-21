import json
import sqlite3
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import test_workflow_plan_representative_http as plan_fixture
from project_data_service.workflow_plan_idle import ARTIFACT, FOLDER_READINESS, PATH, WRITE_SCHEMA, IdlePlan
from project_data_service.workflow_portrait_representative import (
    ARTIFACT as IMAGE_ARTIFACT, PATH as IMAGE_PATH, RepresentativePortrait,
)
from test_workflow_delete_http import digest, encoded, handler
from test_workflow_portrait_representative_http import png


class Tests(unittest.TestCase):
    start=plan_fixture.Tests.start
    post=plan_fixture.Tests.post
    plan=plan_fixture.Tests.plan
    approve=plan_fixture.Tests.approve
    preset=plan_fixture.Tests.preset
    publish_plan=plan_fixture.Tests.publish
    read_plan=plan_fixture.Tests.read

    def start_new_writer(self):
        self.start_idle_writer()

    def start_idle_writer(self,*,workflow_idle_portrait=False,workflow_direction_portrait=False,workflow_native_media=False):
        from pathlib import Path
        from project_data_service.filex_npc_authority import FilexCurrentNPC
        from project_data_service.filex_npc_store import FilexNPCStore
        from project_data_service.pds_owned_transport import OwnedNPCTransport
        from project_data_service.workflow_preset import WorkflowPreset
        _,url=self.start(handler(self.writer,self.original_token,workflow_delete=True,workflow_preset=True,
                                 workflow_representative=True,workflow_portrait=True,workflow_idle_plan=True,
                                 workflow_idle_portrait=workflow_idle_portrait,workflow_direction_portrait=workflow_direction_portrait,workflow_native_media=workflow_native_media))
        cfg={'schemaVersion':'pds-filex-cas.v1','projectId':'binari','scope':'npc','enabled':True,
             'endpoint':url,'tokenFile':str(Path(self.writer.root).parent/'writer-token')}
        s=FilexCurrentNPC(FilexNPCStore(lambda:OwnedNPCTransport(cfg)))
        self.pds.workflow_preset=WorkflowPreset(s,lambda c: self.pb if self.running and c==self.claim else None)

    def setUp(self):
        plan_fixture.Tests.setUp(self)
        status,rep=self.read_plan();self.assertEqual(status,200)
        self.representative_claim=dict(self.c);self.representative_basis=rep['basis']
        self.assertEqual(self.publish_plan(rep['basis']),(200,{'ok':True}))
        parent_id=self.c['taskId']
        self.c['taskId']='workflow.task.'+digest([self.c['officialRequestId'],'planning_idle'])
        self.c['claimStamp']=4
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE workflow_tasks SET status='completed' WHERE task_id=?",(parent_id,))
            db.execute("""INSERT INTO workflow_tasks VALUES(?,?,'planning_idle','npc.plan-idle',
                       'document',?,NULL,4,?,'running')""",
                       (self.c['taskId'],self.c['officialRequestId'],encoded({'npcId':self.c['npcId']}).decode(),parent_id))
        s=self.pds.workflow_plan_representative
        self.pds.workflow_plan_idle=IdlePlan(s.source,s.verifier)
        self.pds.workflow_portrait_representative=RepresentativePortrait(s.source,s.verifier)

    def read(self,claim=None):
        return self.post({'claim':self.c if claim is None else claim,'inputMode':'folder-snapshot'},path=PATH+'/read')

    def artifact(self,basis,content='새 아이들 포즈 기획'):
        return {'schemaVersion':ARTIFACT,'taskId':self.c['taskId'],'projectId':'binari','npcId':self.c['npcId'],
                'categoryId':basis['categoryId'],'content':content}

    def publish(self,basis,artifact=None):
        return self.post({'claim':self.c,'basis':basis,'artifact':artifact or self.artifact(basis)},path=PATH)

    def prepare_image(self):
        # Exercise real independently authorized PDS image write, including
        # whole-project metadata + same-NPC PNG, after idle has frozen inputs.
        claim={**self.c,'taskId':'workflow.task.'+digest([self.c['officialRequestId'],'portrait_representative']),
               'claimStamp':5}
        with sqlite3.connect(self.db) as db:
            db.execute("""INSERT INTO workflow_tasks VALUES(?,?,'portrait_representative',
                       'npc.portrait-representative','image',?,NULL,5,?,'running')""",
                       (claim['taskId'],claim['officialRequestId'],encoded({'npcId':claim['npcId']}).decode(),
                        self.representative_claim['taskId']))
        status,view=self.post({'claim':claim},path=IMAGE_PATH+'/read')
        self.assertEqual(status,200)
        import base64,hashlib
        raw=png(100)
        artifact={'schemaVersion':IMAGE_ARTIFACT,'taskId':claim['taskId'],'projectId':'binari','npcId':claim['npcId'],
                  'sha256':hashlib.sha256(raw).hexdigest(),'base64':base64.b64encode(raw).decode()}
        return {'claim':claim,'basis':view['basis'],'artifact':artifact},raw

    def image_write(self):
        packet,raw=self.prepare_image()
        self.assertEqual(self.post(packet,path=IMAGE_PATH),(200,{'ok':True}))
        return raw

    def test_real_claim_snapshot_scoped_idle_write_after_image_and_no_receipt(self):
        with urlopen(self.base+FOLDER_READINESS,timeout=3) as response:
            self.assertEqual(json.load(response),{'enabled':True,'schemaVersion':WRITE_SCHEMA})
        status,view=self.read();self.assertEqual(status,200)
        self.assertEqual(set(view),{'basis','character','presetContext','representativeContent'})
        self.assertEqual(view['representativeContent'],'새 대표 포즈 기획')
        self.assertEqual(view['character']['npcId'],'npc.fresh')
        context=json.loads(view['presetContext'])
        self.assertEqual(set(context),{'creationBasis','planningPreset','category','chapters','status'})
        b=view['basis'];self.assertEqual(context['category']['id'],b['categoryId'])
        self.assertEqual(context['category']['content'],'')
        self.assertEqual(context['category']['planningRole'],'portrait.idle')
        self.assertEqual(set(b),{'categoryId'})
        original_revision=self.writer.snapshot()['documents']['metadata.json']['_pdsCurrent']['revision']
        raw=self.image_write()
        status,fresh=self.read();self.assertEqual(status,200)
        self.assertEqual(fresh['basis'],b)
        self.assertEqual(self.writer.snapshot()['documents']['metadata.json']['_pdsCurrent']['revision'],original_revision+1)

        rp=self.writer.receipt_path(self.c['taskId'])
        self.writer.write(rp,b'{"history":"not input"}')
        before=self.writer.owner_files('npc.fresh')
        other=self.writer.owner_files('npc.other');planner=self.db.read_bytes()
        with patch.object(self.writer,'receipt_path',side_effect=AssertionError('no receipt')), \
             patch.object(self.writer,'admit',side_effect=AssertionError('no lease')):
            self.assertEqual(self.publish(b),(200,{'ok':True}))
        self.assertEqual(rp.read_bytes(),b'{"history":"not input"}')
        after=self.writer.owner_files('npc.fresh')
        target='npc.fresh/planning/'+b['categoryId']+'.json'
        self.assertEqual(set(before),set(after))
        self.assertEqual({k for k in before if before[k]!=after[k]}, {target,'npc.fresh/.owned-assets.json'})
        self.assertEqual(json.loads(after[target]),{**json.loads(before[target]),'content':'새 아이들 포즈 기획'})
        self.assertEqual(after['npc.fresh/assets/portraits/representative/portrait.png'],raw)
        self.assertEqual(self.writer.owner_files('npc.other'),other)
        self.assertEqual(self.db.read_bytes(),planner)
        self.assertEqual(self.writer.generation('npc.fresh')['activeLeases'],0)
        self.assertEqual(self.writer.snapshot()['documents']['metadata.json']['_pdsCurrent']['revision'],original_revision+2)

    def test_no_start_to_save_input_comparison(self):
        _,view=self.read();b=view['basis']
        parent=self.representative_claim
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE workflow_tasks SET status='running' WHERE task_id=?",(parent['taskId'],))
        status,rep=self.post({'claim':parent,'inputMode':'folder-snapshot'},path='/v1/npcs/ownership/workflow-plan-representative/read')
        self.assertEqual(status,200)
        rep_b=rep['basis']
        artifact={**self.artifact(rep_b,'대표 기획 새 버전'),'taskId':parent['taskId'],
                  'schemaVersion':plan_fixture.ARTIFACT}
        self.assertEqual(self.post({'claim':parent,'basis':rep_b,'artifact':artifact},
                                   path='/v1/npcs/ownership/workflow-plan-representative'),(200,{'ok':True}))
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE workflow_tasks SET status='completed' WHERE task_id=?",(parent['taskId'],))
        self.assertEqual(self.publish(b),(200,{'ok':True}))
        self.assertEqual(self.read()[1]['representativeContent'],'대표 기획 새 버전')

    def test_reject_foreign_claim_parent_sibling_scope_and_untrusted_transport(self):
        self.assertEqual(self.read({**self.c,'claimStamp':5})[0],403)
        self.assertEqual(self.read({**self.c,'npcId':'npc.other'})[0],403)
        self.assertEqual(self.post({'claim':self.c},path=PATH+'/read')[0],409)
        self.assertEqual(self.post({'claim':self.c,'inputMode':'legacy'},path=PATH+'/read')[0],409)
        for path,headers in [(PATH,{}),(FOLDER_READINESS+'&inputMode=folder-snapshot',{}),
                             (FOLDER_READINESS,{'Origin':'http://localhost'})]:
            with self.assertRaises(HTTPError) as failure:urlopen(Request(self.base+path,headers=headers),timeout=3)
            self.assertEqual(failure.exception.code,403);failure.exception.close()
        _,view=self.read();b=view['basis'];a=self.artifact(b)
        before=self.writer.snapshot()['sourceVersion']
        for key,value in [('revision',1),('sourceVersion','0'*64),('generationId','0'*48),('inputSha256','0'*64)]:
            self.assertEqual(self.publish({**b,key:value},a)[0],409)
        self.assertEqual(self.publish({**b,'categoryId':self.representative_basis['categoryId']})[0],409)
        self.assertEqual(self.publish(b,{**a,'content':' '*3})[0],409)
        self.assertEqual(self.publish(b,{**a,'schemaVersion':plan_fixture.ARTIFACT})[0],409)
        for sql,args in [
            ("UPDATE workflow_tasks SET worker_type='image' WHERE task_id=?",(self.c['taskId'],)),
            ("UPDATE workflow_tasks SET worker_type='document',depends_on_task_id=? WHERE task_id=?",(self.preset_id,self.c['taskId'])),
            ("UPDATE workflow_tasks SET depends_on_task_id=? WHERE task_id=?",(self.representative_claim['taskId'],self.c['taskId'])),
        ]:
            with sqlite3.connect(self.db) as db:db.execute(sql,args)
            if args[0]==self.representative_claim['taskId']:
                with sqlite3.connect(self.db) as db:db.execute("UPDATE workflow_tasks SET status='failed' WHERE task_id=?",(args[0],))
            self.assertEqual(self.publish(b,a)[0],403)
        self.assertEqual(self.writer.snapshot()['sourceVersion'],before)
        self.assertFalse(self.writer.receipt_path(self.c['taskId']).exists())

    def test_no_model_start_snapshot_or_global_cas_when_other_write_wins_first(self):
        from project_data_service.pds_owned_transport import OwnedNPCTransport
        _,view=self.read();b=view['basis']
        send=OwnedNPCTransport.workflow_plan_idle
        packet,raw=self.prepare_image()
        def interleave(tx,binding,content):
            self.assertEqual(self.post(packet,path=IMAGE_PATH),(200,{'ok':True}))
            self.assertEqual(set(binding),{'officialRequestId','taskId','projectId','npcId','payloadSha256','categoryId'})
            return send(tx,binding,content)
        before=self.writer.snapshot()['documents']['metadata.json']['_pdsCurrent']['revision']
        with patch.object(OwnedNPCTransport,'workflow_plan_idle',interleave), \
             patch.object(self.pds.workflow_plan_idle,'_current',side_effect=AssertionError('no write-time PDS read')):
            self.assertEqual(self.publish(b),(200,{'ok':True}))
        target=self.writer.safe('npc.fresh/planning/'+b['categoryId']+'.json')
        self.assertEqual(json.loads(target.read_bytes())['content'],'새 아이들 포즈 기획')
        self.assertEqual(self.writer.owner_files('npc.fresh')['npc.fresh/assets/portraits/representative/portrait.png'],raw)
        self.assertEqual(self.writer.snapshot()['documents']['metadata.json']['_pdsCurrent']['revision'],before+2)

    def test_denied_without_writer_opt_in(self):
        _,uri=self.start(handler(self.writer,self.original_token,workflow_representative=True))
        req=Request(uri+'/v1/workflow/plan-idle',headers={'Authorization':'Bearer '+self.original_token,
                    'Content-Type':'application/json'},data=b'{}')
        with self.assertRaises(HTTPError) as failure:urlopen(req,timeout=3)
        self.assertEqual(failure.exception.code,404);failure.exception.close()
