import base64
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from project_data_service.filex_npc_authority import FilexCurrentNPC
from project_data_service.filex_read import canonical
from project_data_service.pds_owned_transport import OwnedNPCTransport
from project_data_service.workflow_delete_claim import ClaimDenied, PrivatePlannerClaim
from project_data_service.workflow_native_media import NativeMedia


class Writer(OwnedNPCTransport):
    def __init__(self, data):
        self.data=data
        self.aid=hashlib.sha256(data).hexdigest()+'.png'
        self.scope=True
        self.staging=True
        self.bad_asset=False
        self.calls=[]
        self.pin={'generationId':'a'*48,'portraitAssetId':self.aid,'resultBasisSha256':'c'*64}
    def __enter__(self):
        return self
    def __exit__(self,*_):
        pass
    def capabilities(self):
        return {'workflowNativePNGMediaV1':True,'workflowSelectedMotionV1':True,
                'workflowRegenerateMotionV1':self.staging}
    def asset_get(self,aid):
        self.calls.append(('canonical','asset',aid))
        return self.data
    def workflow_media(self,kind,action,binding,artifact=None):
        assert binding['regeneration'] is True
        self.calls.append((kind,action,binding,artifact))
        common={key:binding[key] for key in ('motion','direction','posePreset','assetSetSha256')}
        if action=='read' and kind=='animation':
            return {**common,**self.pin}
        if action=='read':
            return {**common,'sequence':{'frames':[{'assetId':self.aid if self.scope else '6'*64+'.png'}]}}
        if action=='asset':
            return {'assetId':self.aid,'sha256':self.aid[:-4],'bytes':len(self.data),
                    'base64':base64.b64encode(b'wrong' if self.bad_asset else self.data).decode()}
        return {'ok':True}


class TestAnimationRegenerateAuthority(unittest.TestCase):
    def setUp(self):
        work=TemporaryDirectory()
        self.addCleanup(work.cleanup)
        dbpath=Path(work.name).resolve()/'planner.sqlite'
        fd=os.open(dbpath,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        self.db=dbpath
        source=Path(__file__).resolve().parents[2]/'contracts/npc-animation-regenerate.v1.fixture.json'
        golden=json.loads(source.read_bytes())
        self.row=golden['row']; self.plan=golden['plan']
        oid=self.row['officialRequestId']; self.stamp=self.row['registeredAt']+5
        with sqlite3.connect(self.db) as db:
            db.executescript('''CREATE TABLE workflow_runs(official_request_id,project_id,request_type,schema_version,
              payload,payload_sha256,registered_at,state,updated_at,reason);
              CREATE TABLE workflow_tasks(task_id,official_request_id,task_key,task_type,worker_type,task_input,
              result_ref,updated_at,depends_on_task_id,status);''')
            row=self.row
            db.execute("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,?,'planned',?,NULL)",(
                oid,row['projectId'],row['requestType'],row['schemaVersion'],self.plan['payload'],
                row['payloadSha256'],row['registeredAt'],self.stamp))
            for task in self.plan['tasks']:
                db.execute("INSERT INTO workflow_tasks VALUES(?,?,?,?,?,?,NULL,?,?,?)",(
                    task['id'],oid,task['key'],task['type'],task['workerType'],canonical(task['input']).decode(),
                    self.stamp,task['dependsOn'],'running'))
        st=self.db.stat()
        self.verifier=PrivatePlannerClaim(str(self.db),st.st_dev,st.st_ino)
        self.writer=Writer(b'fixture Bytes')
        motion=SimpleNamespace(selected_asset_sets=lambda rows:{r['presetId']:('b'*64,{d:'d'*64 for d in
            ('n','ne','e','se','s','sw','w','nw')}) for r in rows})
        self.service=NativeMedia(FilexCurrentNPC(SimpleNamespace(factory=lambda:self.writer)),self.verifier,motion)

    def task(self,kind,pose='combat',direction='e'):
        want='animation' if kind=='animation' else 'postprocess'
        return next(t for t in self.plan['tasks'] if t['workerType']==want and
                    t['input']['posePreset']==pose and t['input']['direction']==direction)

    def claim(self,task):
        fields={key:self.row[key] for key in ('officialRequestId','projectId','payloadSha256')}
        return {**fields,'npcId':self.row['payload']['npcId'],'taskId':task['id'],'claimStamp':self.stamp}

    def sql(self,query,params=()):
        with sqlite3.connect(self.db) as db:db.execute(query,params)

    def test_exact_NULL_animation_and_completed_predecessor_frame_claim(self):
        anim,frames=self.task('animation'),self.task('frames')
        self.assertIsNone(anim['dependsOn'])
        a,f=self.claim(anim),self.claim(frames)
        before=self.db.read_bytes()
        read=self.service.run('animation','read',{'claim':a})
        self.assertEqual(read,{'motion':'sword-attack-flat','direction':'e','posePreset':'combat','assetSetSha256':'b'*64,
                               **self.writer.pin,'referenceSha256':'d'*64,'referencePresetHash':'b'*64})
        with self.assertRaises(ClaimDenied):self.service.run('frames','read',{'claim':f})
        with self.assertRaises(ClaimDenied):self.service.run('animation','read',{'claim':{**a,'npcId':'npc.wrong'}})
        with self.assertRaises(ClaimDenied):self.service.run('animation','read',{'claim':{**a,'regeneration':True}})
        with self.assertRaises(ValueError):self.service.run('animation','publish',{'claim':a,
            'artifact':{'fps':24,'width':8,'height':8,'frames':[],'source':{**self.writer.pin,'resultBasisSha256':'INVALID'}}})
        self.assertEqual(self.service.run('animation','publish',{'claim':a,
            'artifact':{'fps':24,'width':8,'height':8,'frames':[],'source':self.writer.pin}}),{'ok':True})
        self.assertEqual(self.db.read_bytes(),before)
        self.sql("UPDATE workflow_tasks SET status='completed' WHERE task_id=?",(a['taskId'],))
        self.assertEqual(self.service.run('frames','read',{'claim':f})['motion'],'sword-attack-flat')
        self.assertEqual(self.service.run('frames','asset',{'claim':f,'assetId':self.writer.aid}),self.writer.data)
        self.assertFalse(any(call[0]=='canonical' for call in self.writer.calls))
        self.writer.bad_asset=True
        with self.assertRaisesRegex(ValueError,'WORKFLOW_STAGE_ASSET_HASH'):
            self.service.run('frames','asset',{'claim':f,'assetId':self.writer.aid})
        self.writer.scope=False
        with self.assertRaisesRegex(ValueError,'WORKFLOW_MEDIA_ASSET_SCOPE'):
            self.service.run('frames','asset',{'claim':f,'assetId':self.writer.aid})
        self.sql("UPDATE workflow_tasks SET depends_on_task_id=? WHERE task_id=?",('wrong',a['taskId']))
        with self.assertRaises(ClaimDenied):self.service.run('frames','read',{'claim':f})

    def test_regeneration_never_uses_old_selected_writer_fallback(self):
        a=self.claim(self.task('animation','idle','n'))
        self.writer.staging=False
        with self.assertRaisesRegex(ValueError,'WORKFLOW_MEDIA_WRITER'):
            self.service.run('animation','read',{'claim':a})
        self.assertEqual(self.writer.calls,[])
        self.writer.staging=True
        self.sql("UPDATE workflow_tasks SET depends_on_task_id='untrusted' WHERE task_id=?",(a['taskId'],))
        with self.assertRaises(ClaimDenied):self.service.run('animation','read',{'claim':a})
        self.assertEqual(self.writer.calls,[])
