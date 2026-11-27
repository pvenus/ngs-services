"""The actual four-document workflow writes native Markdown on opt-in only."""
import hashlib
import json
import sqlite3
import unittest
from pathlib import Path

import test_workflow_plan_representative_http as rep
import test_workflow_shared_document_chain_http as fixture
from test_workflow_delete_http import digest,encoded
from npc_planning_markdown import planning_projection,planning_write_bytes
from server import Conflict
from project_data_service.workflow_portrait_representative import PATH,RepresentativePortrait

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
        rep.Tests.start_new_writer(self,workflow_shared_plan=True,workflow_portrait=True,
                                  workflow_npc_planning_markdown=True)

    def setUp(self):
        fixture.Tests.setUp(self)
        p=self.pds.workflow_plan_representative
        self.pds.workflow_portrait_representative=RepresentativePortrait(p.source,p.verifier)

    def test_native_four_roles_next_document_and_image_reads(self):
        npc=self.c['npcId'];oid=self.c['officialRequestId']
        outputs={stage:'# '+stage+'\n\n명시적으로 저장된 Markdown' for stage in fixture.STAGES}
        dependency={'overview':{},'portrait-art':{'overviewContent':outputs['overview']},
                    'representative':{'portraitArtContent':outputs['portrait-art']},
                    'idle':{'representativeContent':outputs['representative'],'portraitArtContent':outputs['portrait-art']}}
        roles=['overview','portrait.art','portrait.representative','portrait.idle']
        other=self.writer.owner_files('npc.other');physical=[]
        for role,stage in zip(roles,fixture.STAGES):
            self.state(stage,'running')
            code,args=self.read(stage);self.assertEqual(code,200)
            self.assertEqual({k:v for k,v in args.items()if k.endswith('Content')},dependency[stage])
            self.assertEqual(self.publish(stage,args['basis'],outputs[stage]),(200,{'ok':True}))
            self.assertEqual(self.publish(stage,args['basis'],outputs[stage]),(200,{'ok':True}))
            path=f"{npc}/planning/{args['basis']['categoryId']}"
            meta=json.loads(self.writer.safe(path+'.json').read_bytes())
            raw=self.writer.safe(path+'/content.md').read_bytes()
            self.assertEqual(meta['planningRole'],role)
            self.assertEqual(meta['contentFormat'],'markdown.v1')
            self.assertEqual(meta['contentSha256'],hashlib.sha256(raw).hexdigest())
            self.assertNotIn('content',meta)
            self.assertEqual(raw.decode(),outputs[stage]);physical.append(path+'/content.md')
            self.state(stage,'completed')
        self.assertEqual({k for k in self.writer.owner_files(npc) if k.endswith('/content.md')},set(physical))
        self.assertEqual(self.writer.owner_files('npc.other'),other)
        # An unrelated new role in the same snapshot must not block portrait input.
        for suffix,role in [('combat','portrait.combat'),('future','portrait.future')]:
            cid='category.test-'+suffix
            relative=f'{npc}/planning/{cid}.json'
            row={'id':cid,'name':suffix,'planningRole':role,'content':'# '+suffix}
            for key,raw in planning_write_bytes(relative,row).items():
                path=self.writer.safe(key)
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(raw)
            self.assertEqual(self.writer.snapshot()['documents'][relative],row)
        img='workflow.task.'+digest([oid,'portrait_representative'])
        with sqlite3.connect(self.db)as db:
            db.execute("INSERT INTO workflow_tasks VALUES(?,?,'portrait_representative','npc.portrait-representative','image',?,NULL,15,?,'running')",
                (img,oid,encoded({'npcId':npc}).decode(),self.ids['representative']))
        claim={**self.c,'taskId':img,'claimStamp':15}
        code,result=self.post({'claim':claim},path=PATH+'/read')
        self.assertEqual(code,200)
        self.assertEqual(result['planningContent'],outputs['representative'])

    def test_approved_retry_replaces_existing_markdown_but_completed_claim_cannot(self):
        stage='overview'
        self.state(stage,'running')
        status,view=self.read(stage);self.assertEqual(status,200)
        basis=view['basis']
        self.assertEqual(self.publish(stage,basis,'# First body'),(200,{'ok':True}))
        self.assertEqual(self.publish(stage,basis,'# Retried body'),(200,{'ok':True}))
        key=f"{self.c['npcId']}/planning/{basis['categoryId']}"
        content=self.writer.safe(key+'/content.md')
        self.assertEqual(content.read_bytes(),b'# Retried body')
        pointer=json.loads(self.writer.safe(key+'.json').read_bytes())
        self.assertEqual(pointer['contentSha256'],hashlib.sha256(content.read_bytes()).hexdigest())
        self.state(stage,'completed')
        self.assertEqual(self.publish(stage,basis,'# Unauthorized after completion')[0],403)
        self.assertEqual(content.read_bytes(),b'# Retried body')

    def test_markdown_pointer_still_checks_file_integrity(self):
        relative='npc.new/planning/category.test.json'
        row={'id':'category.test','planningRole':'future','content':'# Original'}
        files=planning_write_bytes(relative,row)
        pointer=json.loads(files[relative])
        with self.assertRaisesRegex(Conflict,'markdown proof'):
            planning_projection(relative,pointer,lambda key:b'# Damaged')
        with self.assertRaisesRegex(Conflict,'markdown index'):
            planning_projection(relative,{**pointer,'content':'shadow'},files.__getitem__)
