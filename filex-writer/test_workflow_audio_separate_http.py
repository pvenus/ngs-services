"""Actual PDS/Filex canonical round trip, without invoking an audio model."""
import json
import sqlite3
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import test_workflow_audio_http as fixture
from concept_store import digest, encoded
from project_data_service.workflow_audio import PREFIX, REJECTION
from project_data_service.workflow_audio_separate import PATH, PAYLOAD, SCHEMA, WorkflowAudioSeparate


class Tests(unittest.TestCase):
    def setUp(self):
        self.audio=fixture.Tests()
        self.addCleanup(self.audio.doCleanups)
        self.audio.setUp()
        self.pds,self.db,self.root=self.audio.pds,self.audio.db,self.audio.root
        self.base=f'http://127.0.0.1:{self.pds.server_port}'+PATH
        self.pds.workflow_audio_separate=WorkflowAudioSeparate(self.audio.source,self.audio.auth)
        self.original=self.audio.plan()
        self.source_id=self.original['officialRequestId']
        self.source_wave=fixture.wav()
        body=self.audio.body(self.original,self.source_wave)
        self.assertEqual(self.audio.call(body)[0],200)
        self.complete(self.original,reason='RS_COMPLETED')

    def complete(self,claim,reason=None):
        oid=claim['officialRequestId']; ref=PREFIX+oid+'/metadata.json'
        with sqlite3.connect(self.db) as q:
            q.execute("UPDATE workflow_tasks SET status='completed',result_ref=? WHERE task_id=?",(ref,claim['taskId']))
            q.execute("UPDATE workflow_runs SET reason=? WHERE official_request_id=?",(reason,oid))

    def plan(self,key='derived',source=None,sha=None):
        oid='request.official.'+fixture.sha(key.encode())
        tid='workflow.task.'+digest([oid,'audio_separate'])
        binding={'sourceOfficialRequestId':source or self.source_id,'sourceSha256':sha or fixture.sha(self.source_wave)}
        p={'schemaVersion':PAYLOAD,'projectId':'binari',**binding}
        with sqlite3.connect(self.db) as q:
            q.execute("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,1,'planned',1,NULL)",
                      (oid,'binari','audio.separate',PAYLOAD,encoded(p).decode(),digest(p)))
            q.execute("INSERT INTO workflow_tasks VALUES(?,?,'audio_separate','audio.separate','audio',?,NULL,2,NULL,'running')",
                      (tid,oid,encoded(binding).decode()))
        self.output=self.root/oid
        return {'officialRequestId':oid,'taskId':tid,'projectId':'binari','payloadSha256':digest(p),'claimStamp':2}

    def call(self,body=None,suffix='',headers=None):
        return self.audio.call(body,suffix,base=self.base,headers=headers)

    def source_files(self):
        return {f.name:f.read_bytes() for f in (self.root/self.source_id).iterdir()}

    def test_scope_binary_publish_and_exact_replay_without_touching_source(self):
        self.assertEqual(self.call(),(200,{'enabled':True,'schemaVersion':SCHEMA}))
        claim=self.plan()
        db_before=self.db.read_bytes()
        before=self.source_files()
        code,view=self.call({'claim':claim},'/read')
        self.assertEqual(code,200)
        proof={'sourceOfficialRequestId':self.source_id,'sourceSha256':fixture.sha(self.source_wave)}
        self.assertEqual(view,{**proof,'bytes':len(self.source_wave),'mimeType':'audio/wav'})
        rq=Request(self.base+'/asset',data=encoded({'claim':claim}),headers={'Content-Type':'application/json'})
        with urlopen(rq,timeout=3) as r:
            self.assertEqual(r.headers['Content-Type'],'audio/wav')
            self.assertEqual(r.headers['Cache-Control'],'no-store')
            self.assertEqual(int(r.headers['Content-Length']),len(self.source_wave))
            self.assertEqual(r.read(),self.source_wave)
        filex=Request(self.audio.writer_url+'/v1/workflow/audio-separate/asset',data=encoded({}),
                      headers={'Content-Type':'application/json'})
        with self.assertRaises(HTTPError) as caught:urlopen(filex,timeout=3)
        self.assertEqual(caught.exception.code,401);caught.exception.close()
        wave=fixture.wav(seconds=9.8,amplitude=21)
        body={'claim':claim,'artifact':fixture.audio(wave)}
        result={'ok':True,'resultRef':PREFIX+claim['officialRequestId']+'/metadata.json'}
        self.assertEqual(self.call(body),(200,result))
        self.assertEqual(self.call(body),(200,result))
        self.assertEqual(self.source_files(),before)
        self.assertEqual(self.db.read_bytes(),db_before)
        self.assertEqual({f.name for f in self.output.iterdir()},{'audio.wav','metadata.json'})
        self.assertEqual((self.output/'audio.wav').read_bytes(),wave)
        meta=json.loads((self.output/'metadata.json').read_bytes())
        self.assertEqual((meta['separation'],meta['taskId'],meta['title'],meta['bpmBasis']),
                         (proof,claim['taskId'],'Official audio fixture (반주)','unavailable'))
        self.assertEqual(meta['source'],json.loads(before['metadata.json'])['source'])
        self.assertEqual(meta['audio']['durationSeconds'],9.8)
        self.assertEqual(self.call({**body,'artifact':fixture.audio(fixture.wav(amplitude=7))})[0],409)
        self.assertEqual((self.output/'audio.wav').read_bytes(),wave)

    def test_missing_scope_wrong_stamp_wrong_sha_or_changed_original_prevents_new_output(self):
        claim=self.plan('bad-sha',sha='0'*64)
        self.assertEqual(self.call({'claim':claim},'/read'),(422,{'error':REJECTION}))
        self.assertEqual(self.call({'claim':claim},'/asset'),(422,{'error':REJECTION}))
        self.assertFalse(self.output.exists())
        claim=self.plan()
        for changes in ({'claimStamp':3},{'projectId':'other'},{'sourceSha256':'0'*64}):
            self.assertEqual(self.call({'claim':{**claim,**changes}},'/read'),(422,{'error':REJECTION}))
        self.assertEqual(self.call({'claim':claim,'sourceOfficialRequestId':self.source_id},'/asset'),(422,{'error':REJECTION}))
        self.assertEqual(self.call({'claim':claim},'/read',headers={'Origin':'http://127.0.0.1'})[0],403)
        body={'claim':claim,'artifact':fixture.audio(fixture.wav(amplitude=11))}
        changed=self.root/self.source_id/'audio.wav'
        changed.write_bytes(fixture.wav(amplitude=1))
        self.assertEqual(self.call(body),(422,{'error':REJECTION}))
        self.assertEqual(self.call({'claim':claim},'/asset'),(422,{'error':REJECTION}))
        changed.unlink()
        self.assertEqual(self.call(body),(422,{'error':REJECTION}))
        self.assertFalse(self.output.exists())

    def test_only_completed_source_and_distinct_near_duration_wav(self):
        claim=self.plan()
        with sqlite3.connect(self.db) as q:
            q.execute("UPDATE workflow_tasks SET status='running' WHERE task_id=?",(self.original['taskId'],))
        self.assertEqual(self.call({'claim':claim},'/read'),(422,{'error':REJECTION}))
        self.complete(self.original)
        snapshots=self.source_files()
        for wave in (self.source_wave,fixture.wav(seconds=10.26,amplitude=7),b'RIFF'+bytes(100)):
            self.assertEqual(self.call({'claim':claim,'artifact':fixture.audio(wave)}),(422,{'error':REJECTION}))
        self.assertFalse(self.output.exists())
        self.assertEqual(self.source_files(),snapshots)
        self.assertFalse(any(p.name.startswith('.audio-stage-') for p in self.root.iterdir()))

    def test_remove_only_selected_derived_result_without_removing_source(self):
        claim=self.plan()
        original=self.source_files()
        body={'claim':claim,'artifact':fixture.audio(fixture.wav(amplitude=33))}
        self.assertEqual(self.call(body)[0],200)
        self.complete(claim,reason='RS_COMPLETED')
        wire=self.audio.delete_plan(claim['officialRequestId'])
        code,ack=self.audio.call(wire,base=f'http://127.0.0.1:{self.pds.server_port}'+fixture.DELETE)
        self.assertEqual(code,200,ack)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.source_files(),original)
        archive=self.root/'.deleted'/ack['recoveryId']/'target'
        self.assertEqual((archive/'audio.wav').read_bytes(),fixture.wav(amplitude=33))
