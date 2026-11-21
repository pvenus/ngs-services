import json
import sqlite3
import unittest
from urllib.request import urlopen
from unittest.mock import patch

import test_workflow_plan_representative_http as fixture
from test_workflow_delete_http import digest,encoded
from project_data_service.workflow_plan_idle import IdlePlan

BASE='/v1/npcs/ownership/workflow-plan-'
STAGES=('overview','portrait-art','representative','idle')
KEY=dict(zip(STAGES,('planning_overview','planning_portrait_art','planning_representative','planning_idle')))


class Tests(unittest.TestCase):
    start=fixture.Tests.start
    post=fixture.Tests.post
    approve=fixture.Tests.approve
    preset=fixture.Tests.preset
    plan=fixture.Tests.plan

    def start_new_writer(self):
        fixture.Tests.start_new_writer(self,workflow_shared_plan=True)

    def setUp(self):
        fixture.Tests.setUp(self)
        s=self.pds.workflow_plan_representative
        for stage in ('overview','portrait-art','idle'):
            setattr(self.pds,'workflow_plan_'+stage.replace('-','_'),IdlePlan(s.source,s.verifier,stage=stage))
        oid=self.c['officialRequestId']
        self.ids={stage:'workflow.task.'+digest([oid,key]) for stage,key in KEY.items()}
        self.claims={}
        with sqlite3.connect(self.db) as db:
            parent=self.preset_id
            for i,stage in enumerate(STAGES):
                task=self.ids[stage];stamp=10+i
                db.execute("DELETE FROM workflow_tasks WHERE task_id=?",(task,))
                db.execute("INSERT INTO workflow_tasks VALUES(?,?,?,?, 'document',?,NULL,?,?,'pending')",
                           (task,oid,KEY[stage],'npc.plan-'+stage,encoded({'npcId':self.c['npcId']}).decode(),stamp,parent))
                self.claims[stage]={**self.c,'taskId':task,'claimStamp':stamp}
                parent=task

    def state(self,stage,value):
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE workflow_tasks SET status=? WHERE task_id=?",(value,self.ids[stage]))

    def read(self,stage):
        return self.post({'claim':self.claims[stage],'inputMode':'folder-snapshot'},path=BASE+stage+'/read')

    def publish(self,stage,basis,content='기획 결과',**extra):
        c=self.claims[stage]
        a={'schemaVersion':'workflow.npc-'+stage+'-plan-artifact.v1','taskId':c['taskId'],
           'projectId':'binari','npcId':c['npcId'],'categoryId':basis['categoryId'],'content':content,**extra}
        return self.post({'claim':c,'basis':basis,'artifact':a},path=BASE+stage)

    def test_actual_four_stage_content_chain_and_no_legacy_impact(self):
        for stage in ('overview','portrait-art'):
            with urlopen(self.base+BASE+stage+'?inputMode=folder-snapshot',timeout=3) as res:
                self.assertEqual(json.load(res),{'enabled':True,'schemaVersion':'workflow.'+stage+'-plan-write.v1'})
        other=self.writer.owner_files('npc.other')
        fingerprints={}
        desired={'overview':{},'portrait-art':{'overviewContent':'overview 결과'},
                 'representative':{'portraitArtContent':'portrait-art 결과'},
                 'idle':{'representativeContent':'representative 결과','portraitArtContent':'portrait-art 결과'}}
        for stage in STAGES:
            self.state(stage,'running')
            status,data=self.read(stage);self.assertEqual(status,200)
            self.assertEqual({k:v for k,v in data.items() if k.endswith('Content')},desired[stage])
            self.assertEqual(data['basis'].keys(),{'categoryId'})
            self.assertEqual(json.loads(data['presetContext'])['category']['content'],'')
            path='npc.fresh/planning/'+data['basis']['categoryId']+'.json'
            row=json.loads(self.writer.safe(path).read_bytes())
            if stage in ('overview','portrait-art'):self.assertNotIn('planningRole',row)
            with patch.object(self.writer,'receipt_path',side_effect=AssertionError('not an operation receipt')), \
                 patch.object(self.writer,'admit',side_effect=AssertionError('no worker lease')):
                self.assertEqual(self.publish(stage,data['basis'],stage+' 결과'),(200,{'ok':True}))
            self.state(stage,'completed')
            fingerprints[path]=self.writer.safe(path).read_bytes()
        files=self.writer.owner_files('npc.fresh')
        self.assertEqual({p:files[p] for p in fingerprints},fingerprints)
        self.assertEqual([json.loads(x)['planningRole'] for x in fingerprints.values()],
                         ['overview','portrait.art','portrait.representative','portrait.idle'])
        self.assertEqual(self.writer.owner_files('npc.other'),other)

    def test_reject_partially_staged_graph_bad_claim_and_missing_actual_source(self):
        self.state('portrait-art','running')
        self.assertEqual(self.read('portrait-art')[0],403)
        self.state('overview','completed')
        self.assertEqual(self.read('portrait-art')[0],409) # DB alone does not create source bytes
        c=self.claims['portrait-art']
        act='category.preset.'+digest(['npc.standard','3',self.preset_id,'portrait-art'])[:32]
        self.assertEqual(self.publish('portrait-art',{'categoryId':act})[0],409)
        self.assertEqual(self.post({'claim':{**c,'claimStamp':999},'inputMode':'folder-snapshot'},
                                   path=BASE+'portrait-art/read')[0],403)
        self.state('representative','running')
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE workflow_tasks SET depends_on_task_id=? WHERE task_id=?",(self.preset_id,self.ids['representative']))
        self.assertEqual(self.read('representative')[0],403)
        with sqlite3.connect(self.db) as db:
            db.execute("DELETE FROM workflow_tasks WHERE task_id=?",(self.ids['overview'],))
        self.assertEqual(self.read('representative')[0],403)
