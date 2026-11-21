import base64
import hashlib
import json
import sqlite3
import unittest
from unittest.mock import patch
from urllib.request import urlopen

import test_workflow_plan_idle_http as idle_fixture
from project_data_service.workflow_portrait_representative import IDLE_PATH,DIRECTION_PATH,RepresentativePortrait,VARIANTS
from test_workflow_delete_http import digest,encoded
from test_workflow_portrait_representative_http import png


class Tests(unittest.TestCase):
    start=idle_fixture.Tests.start
    post=idle_fixture.Tests.post
    plan=idle_fixture.Tests.plan
    approve=idle_fixture.Tests.approve
    preset=idle_fixture.Tests.preset
    publish_plan=idle_fixture.Tests.publish_plan
    read_plan=idle_fixture.Tests.read_plan
    read=idle_fixture.Tests.read
    publish=idle_fixture.Tests.publish
    artifact=idle_fixture.Tests.artifact
    prepare_image=idle_fixture.Tests.prepare_image

    def start_new_writer(self):
        idle_fixture.Tests.start_idle_writer(self,workflow_idle_portrait=True,workflow_direction_portrait=True)

    def setUp(self):
        idle_fixture.Tests.setUp(self)
        packet,self.representative= self.prepare_image()
        from project_data_service.workflow_portrait_representative import PATH
        self.assertEqual(self.post(packet,path=PATH),(200,{'ok':True}))
        _,idle=self.read();self.assertEqual(self.publish(idle['basis']),(200,{'ok':True}))
        self.rep_id=packet['claim']['taskId']
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE workflow_tasks SET status='completed' WHERE task_id IN (?,?)",(self.c['taskId'],self.rep_id))
        self.idle=self.claim_portrait('portrait_idle_reference','npc.portrait-idle-reference',self.c['taskId'],8)
        p=self.pds.workflow_portrait_representative
        self.pds.workflow_portrait_idle=RepresentativePortrait(p.source,p.verifier,mode='idle')
        self.pds.workflow_portrait_direction=RepresentativePortrait(p.source,p.verifier,mode='direction')

    def claim_portrait(self,key,kind,parent,stamp,direction=None):
        task='workflow.task.'+digest([self.c['officialRequestId'],key])
        body={'npcId':self.c['npcId']}
        if direction:body['direction']=direction
        with sqlite3.connect(self.db) as db:
            db.execute("""INSERT INTO workflow_tasks VALUES(?,?,?,?, 'image',?,NULL,?,?,'running')""",
                       (task,self.c['officialRequestId'],key,kind,encoded(body).decode(),stamp,parent))
        return {**self.c,'taskId':task,'claimStamp':stamp}

    def upload(self,mode,claim,view,raw):
        path,_,schema,*_=VARIANTS[mode]
        a={'schemaVersion':schema,'taskId':claim['taskId'],'projectId':'binari','npcId':claim['npcId'],
           'sha256':hashlib.sha256(raw).hexdigest(),'base64':base64.b64encode(raw).decode()}
        return self.post({'claim':claim,'basis':view['basis'],'artifact':a},path=path)

    def test_canonical_idle_and_all_eight_directions(self):
        other=self.writer.owner_files('npc.other');prefix='npc.fresh/'
        for mode in ('idle','direction'):
            with urlopen(self.base+VARIANTS[mode][0],timeout=3) as response:
                self.assertEqual(json.load(response),{'enabled':True,'schemaVersion':VARIANTS[mode][1]})
        c,self.idle_view=self.post({'claim':self.idle},path=IDLE_PATH+'/read')
        self.assertEqual(c,200)
        self.assertEqual(set(self.idle_view),{'basis','character','planningContent','reference'})
        self.assertEqual(self.idle_view['basis'].keys(),{'categoryId'})
        self.assertEqual(self.idle_view['planningContent'],'새 아이들 포즈 기획')
        self.assertEqual(base64.b64decode(self.idle_view['reference']['base64']),self.representative)
        png_idle=png(201)
        with patch.object(self.writer,'receipt_path',side_effect=AssertionError('no receipt')), \
             patch.object(self.writer,'admit',side_effect=AssertionError('no lease')):
            self.assertEqual(self.upload('idle',self.idle,self.idle_view,png_idle),(200,{'ok':True}))
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE workflow_tasks SET status='completed' WHERE task_id=?",(self.idle['taskId'],))
        directions=('n','ne','e','se','s','sw','w','nw')
        for k,d in enumerate(directions):
            claim=self.claim_portrait('portrait_'+d,'npc.portrait-direction',self.idle['taskId'],9+k,d)
            code,view=self.post({'claim':claim},path=DIRECTION_PATH+'/read')
            self.assertEqual(code,200);self.assertEqual(view['direction'],d)
            self.assertEqual(base64.b64decode(view['reference']['base64']),png_idle)
            self.assertEqual(view['reference']['assetId'],hashlib.sha256(png_idle).hexdigest()+'.png')
            with patch.object(self.writer,'receipt_path',side_effect=AssertionError('no receipt')):
                self.assertEqual(self.upload('direction',claim,view,png(k+1)),(200,{'ok':True}))
            self.assertFalse(self.writer.receipt_path(claim['taskId']).exists())
        files=self.writer.owner_files('npc.fresh');meta=json.loads(files[prefix+'metadata.json'])
        self.assertEqual(files[prefix+'assets/portraits/representative/portrait.png'],self.representative)
        self.assertEqual(files[prefix+'assets/portraits/idle/portrait.png'],png_idle)
        self.assertEqual(meta['activePortraitId'],hashlib.sha256(self.representative).hexdigest()+'.png')
        self.assertEqual(meta['poseViewKeys'],sorted('idle/portrait/'+d for d in directions))
        for k,d in enumerate(directions):
            slot='idle/portrait/'+d
            raw=files[prefix+'assets/pose-views/'+slot+'/image.png']
            self.assertEqual(raw,png(k+1))
            row=json.loads(files[prefix+'pose-views/'+slot+'.json'])
            self.assertEqual(row['assetId'],hashlib.sha256(raw).hexdigest()+'.png')
            self.assertEqual((row['direction'],row['referenceType'],row['schemaVersion']),
                             (d,'portrait','ngs.npc-pose-view.v1'))
        self.assertEqual(self.writer.owner_files('npc.other'),other)

    def test_only_current_exact_claim_and_prerequisite(self):
        n=self.claim_portrait('portrait_n','npc.portrait-direction',self.idle['taskId'],9,'n')
        self.assertEqual(self.post({'claim':n},path=DIRECTION_PATH+'/read')[0],403)
        self.assertEqual(self.post({'claim':self.idle},path=DIRECTION_PATH+'/read')[0],403)
        self.assertEqual(self.post({'claim':{**self.idle,'claimStamp':99}},path=IDLE_PATH+'/read')[0],403)
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE workflow_tasks SET status='completed' WHERE task_id=?",(self.idle['taskId'],))
        self.assertEqual(self.post({'claim':n},path=DIRECTION_PATH+'/read')[0],409) # no actual reference
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE workflow_tasks SET task_input=? WHERE task_id=?",
                       (encoded({'npcId':'npc.fresh','direction':'ne'}).decode(),n['taskId']))
        self.assertEqual(self.post({'claim':n},path=DIRECTION_PATH+'/read')[0],403)
