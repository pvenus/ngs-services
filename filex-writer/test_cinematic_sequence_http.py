import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import subprocess
import unittest
import zlib
from urllib.error import HTTPError
from urllib.request import Request,urlopen

from project_data_service.filex_cas_transport import FilexCASTransport
from project_data_service.http_server import Handler
from project_data_service.project_folders import ProjectFolders
from project_data_service.recursive_folders import compare,fetch
from project_data_service.workflow_cinematic_sequence import WorkflowCinematicSequence,PATH
from project_data_service.workflow_delete_claim import PrivatePlannerClaim
from project_data_service.workflow_cinematic_playback import WorkflowCinematicPlayback,PATH as PLAYBACK_PATH
from test_scope_registry import UnifiedFixture

REPO=Path(__file__).resolve().parents[1]
OID='request.official.'+'4'*64
PLAYBACK={'durationFrames':132,'speedCurve':[{'frame':0,'rate':1/3},{'frame':90,'rate':0.0000001}],
          'cameraCurve':[{'frame':0,'x':.5,'y':.5,'zoom':1},{'frame':110,'x':.2,'y':.4,'zoom':2}]}

def sha(raw):return hashlib.sha256(raw).hexdigest()
def png(i):
    k=128;h=struct.pack('>IIBBBBB',k,k,8,2,0,0,0);frame=(b'\0'+bytes([30+i,180,70])*k)*k
    def chunk(name,raw):return struct.pack('>I',len(raw))+name+raw+struct.pack('>I',zlib.crc32(name+raw))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',h)+chunk(b'IDAT',zlib.compress(frame))+chunk(b'IEND',b'')

class SequenceFixture(UnifiedFixture):
    def sequence(self):
        root=self.data/'cinematics';folder=root/'sources';folder.mkdir();still=png(0);source=sha(still)
        (folder/(source+'.png')).write_bytes(still)
        payload={'schemaVersion':'ngs.cinematic-sequence-generate.v1','projectId':'binari','sourceSha256':source,
                 'motionPrompt':'저고리 주변의 먹 번짐', 'playback':PLAYBACK}
        db=self.root/'planner.sqlite'
        js="""import {PlannerStore} from './workflow-planner/planner.mjs';
import {canonical,sha} from './workflow-planner/envelope.mjs';
const [file,oid,raw]=process.argv.slice(1),payload=JSON.parse(raw),s=new PlannerStore(file,{cinematicEnabled:true});
const payloadSha256=sha(canonical(payload));
s.stage({officialRequestId:oid,projectId:'binari',requestType:'cinematic.sequence.generate',
 schemaVersion:payload.schemaVersion,payload,payloadSha256,registeredAt:1,status:'registered'});
s.acknowledge(oid);
const taskId=s.db.prepare('SELECT task_id FROM workflow_tasks').get().task_id;
s.db.prepare('UPDATE workflow_tasks SET status=?,updated_at=?').run('running',5);
console.log(JSON.stringify({officialRequestId:oid,projectId:'binari',taskId,payloadSha256,claimStamp:5}));
s.close();"""
        claim=json.loads(subprocess.run(['node','--input-type=module','-e',js,str(db),OID,json.dumps(payload)],
                         cwd=REPO,check=True,capture_output=True).stdout)
        db.chmod(0o600);st=db.stat()
        from http.server import ThreadingHTTPServer
        from threading import Thread
        app=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        writer={'schemaVersion':'pds-filex-cas.v1','projectId':'binari','enabled':True,
                'endpoint':self.origin,'tokenFile':str(self.secret/'media'/'writer_token'),'writerScope':'cinematics'}
        planner=PrivatePlannerClaim(str(db),st.st_dev,st.st_ino)
        app.workflow_cinematic_sequence=WorkflowCinematicSequence(
            planner,writer_factory=lambda:FilexCASTransport(writer),readiness=lambda:True)
        app.workflow_cinematic_playback=WorkflowCinematicPlayback(planner,writer_factory=lambda:FilexCASTransport(writer))
        t=Thread(target=app.serve_forever,daemon=True);t.start()
        def stop():app.shutdown();app.server_close();t.join()
        self.addCleanup(stop)
        def call(suffix,body,expected=200,binary=False,base=PATH):
            q=Request(f'http://127.0.0.1:{app.server_port}'+base+suffix,data=None if body is None else json.dumps(body).encode(),
                      headers={} if body is None else {'Content-Type':'application/json'})
            try:
                with urlopen(q,timeout=10) as r:
                    self.assertEqual(r.status,expected);return r.read() if binary else json.load(r)
            except HTTPError as e:
                with e:self.assertEqual(e.code,expected);return json.load(e)
        return root,db,claim,call,payload,still

    def test_claim_stages_frames_off_catalog_then_commits_exact_full_png_folder(self):
        root,db,claim,call,payload,still=self.sequence()
        self.assertEqual(call('',None),{'schemaVersion':'workflow.cinematic-sequence-write.v1','enabled':True})
        self.assertEqual(call('/read',{'claim':claim}),{k:payload[k] for k in ('sourceSha256','motionPrompt','playback')})
        self.assertEqual(call('/asset',{'claim':claim},binary=True),still)
        frames=[png(i) for i in range(45)]
        metas=[{'index':i,'bytes':len(raw),'sha256':sha(raw)} for i,raw in enumerate(frames)]
        manifest={'schemaVersion':'ngs.cinematic-sequence-result.v1','projectId':'binari','officialRequestId':OID,
                  'sourceSha256':payload['sourceSha256'],'width':128,'height':128,'masterFps':24,'frameCount':45,
                  'frames':metas,'playback':PLAYBACK}
        offer={'claim':claim,'manifest':manifest}
        def put(i,override=None,expected=200):
            raw=frames[i] if override is None else override
            return call('/frame',{'claim':claim,'frame':{'index':i,'bytes':len(raw),'sha256':sha(raw),'base64':base64.b64encode(raw).decode()}},expected)
        final=root/'sequences'/OID
        self.assertEqual(put(0),{'ok':True,'index':0,'sha256':metas[0]['sha256']})
        self.assertEqual(put(0),{'ok':True,'index':0,'sha256':metas[0]['sha256']})
        self.assertFalse(final.exists())
        call('',offer,503)
        self.assertFalse(final.exists())
        call('',{**offer,'claim':{**claim,'claimStamp':6}},403)
        put(0,png(47),503)
        for i in range(1,len(frames)):put(i)
        self.assertFalse(final.exists())
        call('',{**offer,'manifest':{**manifest,'playback':{**PLAYBACK,'durationFrames':140}}},403)
        ack=call('',offer)
        ref='binari/cinematics/sequences/'+OID+'/manifest.json'
        self.assertEqual(ack,{'ok':True,'resultRef':ref})
        self.assertEqual(call('',offer),ack)
        self.assertEqual(put(0),{'ok':True,'index':0,'sha256':metas[0]['sha256']})
        self.assertEqual(json.loads((final/'manifest.json').read_bytes()),manifest)
        self.assertFalse(list((root/'sequences').glob('.staged-*')))
        call('/result',{'officialRequestId':OID},403)
        with sqlite3.connect(db) as conn:conn.execute("UPDATE workflow_tasks SET status='completed',result_ref=?",(ref,))
        self.assertEqual(call('/result',{'officialRequestId':OID}),{'ok':True,'resultRef':ref,'manifest':manifest})
        for f,original in zip(sorted((final/'frames').glob('*.png')),frames):
            self.assertEqual(f.read_bytes(),original)
        self.assertEqual((root/'sources'/(payload['sourceSha256']+'.png')).read_bytes(),still)
        before=(final/'manifest.json').read_bytes()
        self.assertEqual(call('',None,base=PLAYBACK_PATH),{'enabled':True,'schemaVersion':'workflow.cinematic-playback-write.v1'})
        self.assertEqual(call('/target-status',{'sequenceId':OID,'expectedRevision':0},base=PLAYBACK_PATH),
                         {'ok':True,'sequenceId':OID,'revision':0})
        def stage_update(suffix,expected):
            original={'schemaVersion':'ngs.cinematic-playback-update.v1','projectId':'binari','sequenceId':OID,
                      'expectedRevision':expected,'playback':{**PLAYBACK,'durationFrames':200+expected}}
            edit_id='request.official.'+suffix*64
            js="""import {PlannerStore} from './workflow-planner/planner.mjs';
import {canonical,sha} from './workflow-planner/envelope.mjs';
const [file,oid,raw]=process.argv.slice(1),payload=JSON.parse(raw),s=new PlannerStore(file,{cinematicEnabled:true});
const payloadSha256=sha(canonical(payload));
s.stage({officialRequestId:oid,projectId:'binari',requestType:'cinematic.playback.update',
 schemaVersion:payload.schemaVersion,payload,payloadSha256,registeredAt:1,status:'registered'});
s.acknowledge(oid);
const taskId=s.db.prepare('SELECT task_id FROM workflow_tasks WHERE official_request_id=?').get(oid).task_id;
s.db.prepare('UPDATE workflow_tasks SET status=?,updated_at=? WHERE task_id=?').run('running',6,taskId);
console.log(JSON.stringify({officialRequestId:oid,projectId:'binari',taskId,payloadSha256,claimStamp:6}));
s.close();"""
            cl=json.loads(subprocess.run(['node','--input-type=module','-e',js,str(db),edit_id,json.dumps(original)],
                          cwd=REPO,check=True,capture_output=True).stdout)
            return original,cl
        update,selected=stage_update('5',0)
        path='binari/cinematics/sequences/'+OID+'/timeline.json'
        run={'claim':selected}
        self.assertFalse((final/'timeline.json').exists())
        self.assertEqual(call('',run,base=PLAYBACK_PATH),{'ok':True,'resultRef':path})
        self.assertEqual(call('',run,base=PLAYBACK_PATH),{'ok':True,'resultRef':path})
        state=json.loads((final/'timeline.json').read_bytes())
        self.assertEqual(state,{'schemaVersion':'ngs.cinematic-playback-state.v1','projectId':'binari','sequenceId':OID,
                               'revision':1,'playback':update['playback']})
        _,conflicting=stage_update('6',0)
        self.assertEqual(call('',{'claim':conflicting},409,base=PLAYBACK_PATH),{'error':'WORKFLOW_CINEMATIC_PLAYBACK_CONFLICT'})
        self.assertEqual(call('/target-status',{'sequenceId':OID,'expectedRevision':0},409,base=PLAYBACK_PATH),
                         {'error':'WORKFLOW_CINEMATIC_PLAYBACK_CONFLICT'})
        with sqlite3.connect(db) as conn:conn.execute("UPDATE workflow_tasks SET status='completed',result_ref=? WHERE task_id=?",(path,selected['taskId']))
        self.assertEqual(call('/result',{'officialRequestId':selected['officialRequestId']},base=PLAYBACK_PATH),
                         {'ok':True,'resultRef':path,'state':state})
        _,next_edit=stage_update('7',1)
        self.assertEqual(call('',{'claim':next_edit},base=PLAYBACK_PATH),{'ok':True,'resultRef':path})
        self.assertEqual(call('/result',{'officialRequestId':selected['officialRequestId']},base=PLAYBACK_PATH),
                         {'ok':True,'resultRef':path,'state':state})
        self.assertEqual(json.loads((final/'timeline.json').read_bytes())['revision'],2)
        self.assertEqual((final/'manifest.json').read_bytes(),before)
        self.assertEqual(len(list((final/'frames').iterdir())),45)
        for f,original in zip(sorted((final/'frames').glob('*.png')),frames):self.assertEqual(f.read_bytes(),original)
        damaged=final/'frames'/'0044.png';damaged.write_bytes(png(18))
        call('/result',{'officialRequestId':OID},503)

if __name__=='__main__':unittest.main()
