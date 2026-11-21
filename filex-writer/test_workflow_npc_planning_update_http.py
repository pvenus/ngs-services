import hashlib
import json
import sqlite3
import unittest
from unittest.mock import patch
from urllib.request import urlopen

import test_workflow_plan_representative_http as rep
import test_workflow_shared_document_chain_http as fixture
from project_data_service.workflow_npc_planning_update import PATH,SCHEMA,PlanningUpdate
from project_data_service.workflow_delete import NPC_ROOT,WorkflowDelete
from project_data_service.single_target_delete import PATH as DELETE, SingleTargetDelete
from test_workflow_delete_http import digest,encoded

class Tests(unittest.TestCase):
    start=fixture.Tests.start
    post=fixture.Tests.post
    approve=fixture.Tests.approve
    preset=fixture.Tests.preset
    plan=fixture.Tests.plan
    state=fixture.Tests.state
    read=fixture.Tests.read
    publish=fixture.Tests.publish

    def start_new_writer(self):
        rep.Tests.start_new_writer(self,workflow_shared_plan=True,workflow_npc_planning_update=True,
                                  workflow_npc_folder_delete=True)
    def setUp(self):
        fixture.Tests.setUp(self)
        p=self.pds.workflow_plan_representative
        self.service=PlanningUpdate(p.source,p.verifier)
        self.pds.workflow_npc_planning_update=self.service
        for stage in fixture.STAGES:
            self.state(stage,'running')
            st,data=self.read(stage);self.assertEqual(st,200)
            self.assertEqual(self.publish(stage,data['basis'],'# '+stage+'\n\n초기 원본'),(200,{'ok':True}))
            self.state(stage,'completed')

    def new_update_claim(self,role='portrait.art',seed='b'):
        oid='request.official.'+seed*64;tid='workflow.task.'+digest([oid,'planning_update'])
        p={'schemaVersion':SCHEMA,'projectId':'binari','npcId':'npc.fresh','role':role,
           'requirements':'설정 문서를 전체적으로 새롭게 보완해 주세요'}
        with sqlite3.connect(self.db)as db:
            db.execute("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,1,'planned',1,NULL)",
                (oid,'binari','npc.planning.update',SCHEMA,encoded(p).decode(),digest(p)))
            db.execute("INSERT INTO workflow_tasks VALUES(?,?,'planning_update','npc.planning.update','document',?,NULL,3,NULL,'running')",
                (tid,oid,encoded({'npcId':'npc.fresh','role':role}).decode()))
        return {'officialRequestId':oid,'taskId':tid,'projectId':'binari','npcId':'npc.fresh','payloadSha256':digest(p),'claimStamp':3}
    def update_read(self,c):
        return self.post({'claim':c},path=PATH+'/read')
    def update(self,c,source,body,**over):
        a={'schemaVersion':'workflow.npc-planning-update-markdown.v1',
           **{k:source[k]for k in ('categoryId','role','generationId')},
           'expectedSourceSha256':source['sourceSha256'],'content':body,**over}
        return self.post({'claim':c,'artifact':a},path=PATH)

    def test_full_source_cas_first_legacy_transition_and_neighbor_unchanged(self):
        with urlopen(self.base+PATH,timeout=3)as r:
            self.assertEqual(json.load(r),{'enabled':True,'schemaVersion':'workflow.npc-planning-update-write.v1'})
        c=self.new_update_claim();st,d=self.update_read(c);self.assertEqual(st,200)
        self.assertEqual(d['source'],'# portrait-art\n\n초기 원본')
        self.assertEqual(d['sourceSha256'],hashlib.sha256(d['source'].encode()).hexdigest())
        self.assertEqual(self.update_read({**c,'claimStamp':99})[0],403)
        self.assertEqual(self.update_read({**c,'npcId':'npc.other'})[0],403)
        old=self.writer.owner_files('npc.fresh');other=self.writer.owner_files('npc.other')
        cid=d['categoryId'];logical=f'npc.fresh/planning/{cid}.json'
        md=f'npc.fresh/planning/{cid}/content.md'
        self.assertEqual(self.update(c,d,'# invalid',role='portrait.idle')[0],403)
        self.assertEqual(self.update(c,d,'# invalid',categoryId='category.neighbor')[0],409)
        self.assertEqual(self.update(c,d,'# invalid',generationId='a'*48)[0],409)
        self.assertEqual(self.update(c,d,'# invalid',expectedSourceSha256='a'*64)[0],409)
        self.assertEqual(self.writer.owner_files('npc.fresh'),old)
        with patch.object(self.writer,'admit',side_effect=AssertionError('no lease')), \
             patch.object(self.writer,'delete_character',side_effect=AssertionError('no delete')):
            self.assertEqual(self.update(c,d,'# 전체 새 문서\n\n변경.'),(200,{'ok':True}))
            self.assertEqual(self.update(c,d,'# 전체 새 문서\n\n변경.'),(200,{'ok':True}))
        after=self.writer.owner_files('npc.fresh')
        self.assertEqual({k for k,v in old.items()if after[k]!=v},{logical,'npc.fresh/.owned-assets.json'})
        self.assertEqual(set(after)-set(old),{md})
        self.assertEqual(after[md].decode(),'# 전체 새 문서\n\n변경.')
        pointer=json.loads(after[logical]);self.assertNotIn('content',pointer)
        self.assertEqual((pointer['contentFormat'],pointer['contentSha256']),
                         ('markdown.v1',hashlib.sha256(after[md]).hexdigest()))
        self.assertEqual(self.writer.owner_files('npc.other'),other)
        snap=self.service.source.store.snapshot()
        self.assertEqual(snap['documents'][logical],{**json.loads(old[logical]),'content':after[md].decode()})
        # Old official document readers follow the same resulting source, not stale JSON.
        self.state('representative','running')
        st,out=self.read('representative');self.assertEqual(st,200)
        self.assertEqual(out['portraitArtContent'],after[md].decode())
        # Existing overwrite callers can't make a duplicate JSON body active.
        self.state('portrait-art','running')
        self.assertEqual(self.publish('portrait-art',{'categoryId':cid},'attempt to revert')[0],409)
        # MD is now exact authority; an out-of-band file edit is never ignored.
        self.writer.safe(md).write_text('changed out of band')
        self.assertNotEqual(self.update_read(c)[0],200)

    def test_second_update_stale_rejected_and_generic_move_preserves_md(self):
        a=self.new_update_claim('overview','a');other=self.new_update_claim('overview','e')
        code,d=self.update_read(a);self.assertEqual(code,200)
        self.assertEqual(self.update(a,d,'# 첫 갱신\n\n내용'),(200,{'ok':True}))
        self.assertEqual(self.update(other,d,'# 오래된 소스의 결과')[0],409)
        self.assertEqual(self.update_read(other)[1]['source'],'# 첫 갱신\n\n내용')
        self.assert_generic_move(d['categoryId'],'# 첫 갱신\n\n내용')

    def assert_generic_move(self,category_id,content):
        # Exercise the exact generic NPC route on this isolated generation only.
        svc=WorkflowDelete(self.service.source,self.service.verifier);self.pds.workflow_delete=svc
        self.pds.single_target_delete=SingleTargetDelete({NPC_ROOT:svc.storage_provider()})
        oid='request.official.'+'f'*64;tid='workflow.task.'+digest([oid,'delete'])
        inp={'npcId':'npc.fresh','scope':'exact-npc-folder','preserveRequestHistory':True,'noOldDataReuse':True}
        payload={'schemaVersion':'ngs.npc-one-stop.replace.v1','projectId':'binari','intent':'fixture exact replace',
                 'character':{**self.art['character'],'npcId':'npc.fresh'},'sourceRefs':['fixture'],'includeCombat':False}
        with sqlite3.connect(self.db)as db:
            db.execute("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,1,'planned',1,NULL)",
                (oid,'binari','npc.workflow',payload['schemaVersion'],encoded(payload).decode(),digest(payload)))
            db.execute("INSERT INTO workflow_tasks VALUES(?,?,'delete','npc.delete-folder','file',?,NULL,3,NULL,'running')",
                (tid,oid,encoded(inp).decode()))
        claim={'officialRequestId':oid,'taskId':tid,'projectId':'binari','npcId':'npc.fresh',
               'payloadSha256':digest(payload),'claimStamp':3}
        result,ack=self.post({'claim':claim,'target':{'root':NPC_ROOT,'path':'npc.fresh','kind':'directory'}},path=DELETE)
        self.assertEqual(result,200)
        relative='planning/'+category_id+'/content.md'
        old=self.writer.root/'.storage-quarantine'/ack['recoveryId']/'target'/relative
        self.assertEqual(old.read_text(),content)
        r=self.writer.receipt(tid)
        self.assertEqual(self.writer.backup_preview(r['backupId'])['documents']['npc.fresh/planning/'+category_id+'.json']['content'],content)
