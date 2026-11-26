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
from project_data_service.workflow_cinematic_project import WorkflowCinematicProject,ORIGIN as PROJECT_ORIGIN,SOURCE,BACKGROUND

REPO=Path(__file__).resolve().parents[1]
OID='request.official.'+'4'*64
PLAYBACK={'durationFrames':132,'speedCurve':[{'frame':0,'rate':1/3},{'frame':90,'rate':0.0000001}],
          'cameraCurve':[{'frame':0,'x':.5,'y':.5,'zoom':1},{'frame':110,'x':.2,'y':.4,'zoom':2}]}

def sha(raw):return hashlib.sha256(raw).hexdigest()
def png(i,width=128,height=128):
    h=struct.pack('>IIBBBBB',width,height,8,2,0,0,0);frame=(b'\0'+bytes([30+i,180,70])*width)*height
    def chunk(name,raw):return struct.pack('>I',len(raw))+name+raw+struct.pack('>I',zlib.crc32(name+raw))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',h)+chunk(b'IDAT',zlib.compress(frame))+chunk(b'IEND',b'')

class SequenceFixture(UnifiedFixture):
    def sequence(self,source_job=False,background=False):
        root=self.data/'cinematics';folder=root/'sources';folder.mkdir();still=png(0);source=sha(still)
        (folder/(source+'.png')).write_bytes(still)
        payload={'schemaVersion':'ngs.cinematic-sequence-generate.v1','projectId':'binari','sourceSha256':source,
                 'motionPrompt':'저고리 주변의 먹 번짐', 'playback':PLAYBACK}
        if source_job:
            payload={'schemaVersion':'ngs.cinematic-source-generate.v1','projectId':'binari',
                     'title':'영적인 문','prompt':'밤의 종이문 틈으로 들어오는 그림자.'}
        if background:
            payload={'schemaVersion':'ngs.background-source-generate.v1','projectId':'binari','title':'빈 한옥','timeOfDay':'해질녘','season':'늦가을','mood':'고요함','color':'먹색','location':'한옥 안채','requirements':'마루와 문턱이 보이게','additionalNotes':''}
        db=self.root/'planner.sqlite'
        js="""import {PlannerStore} from './workflow-planner/planner.mjs';
import {canonical,sha} from './workflow-planner/envelope.mjs';
const [file,oid,raw]=process.argv.slice(1),payload=JSON.parse(raw),s=new PlannerStore(file,{cinematicEnabled:true});
const payloadSha256=sha(canonical(payload));
s.stage({officialRequestId:oid,projectId:'binari',requestType:payload.schemaVersion==='ngs.background-source-generate.v1'?'background.source.generate':payload.schemaVersion==='ngs.cinematic-source-generate.v1'?'cinematic.source.generate':'cinematic.sequence.generate',
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
        app.workflow_cinematic_source=WorkflowCinematicProject(planner,SOURCE,writer_factory=lambda:FilexCASTransport(writer))
        app.workflow_cinematic_background=WorkflowCinematicProject(planner,BACKGROUND,writer_factory=lambda:FilexCASTransport(writer))
        from project_data_service.workflow_file_delete import BACKGROUND_ROOT,storage_provider
        from project_data_service.single_target_delete import SingleTargetDelete
        app.single_target_delete=SingleTargetDelete({BACKGROUND_ROOT:storage_provider(
            planner,lambda:FilexCASTransport(writer),lambda:True,root=BACKGROUND_ROOT)},dedicated=[BACKGROUND_ROOT])
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

    def test_background_animation_group_keeps_pinned_sources_and_exact_independent_sequence_results(self):
        from project_data_service.filex_read import digest
        from media_store import CinematicProjectStore
        root,db,_,call,_,_=self.sequence()
        store=CinematicProjectStore(root)
        O='request.official.'+'9'*64
        raws={v:png(i,1536,1024) for i,v in enumerate(('cinematic','pixel','draft'),1)}
        refs={v:sha(raw) for v,raw in raws.items()}
        (root/'backgrounds').mkdir()
        for v,suffix,key in (('cinematic','시네마틱 원본','background_cinematic_source'),
                             ('pixel','픽셀 보정',None),('draft','픽셀 생성본','background_pixel_generate')):
            oid='request.official.'+digest([O,key]) if key else O
            meta={'schemaVersion':'ngs.background-source-result.v1','projectId':'binari','officialRequestId':oid,
                  'title':'안채 · '+suffix,'sha256':refs[v],'bytes':len(raws[v]),'width':1536,'height':1024}
            store.cinematic_source_generate({'manifest':meta,'base64':base64.b64encode(raws[v]).decode(),
                  'claimStamp':55},background=True)
        selected={'cinematic':refs['cinematic'],'pixel':refs['pixel']}
        pin={'backgroundSha256':refs['pixel'],'references':selected}
        self.assertEqual(call('/background-status',None),{'enabled':True,'schemaVersion':'workflow.background-animation-write.v1'})
        self.assertEqual(call('/background-status',pin),{'ok':True})
        call('/background-status',{**pin,'references':{'cinematic':refs['draft']}},403)
        call('/background-status',{'backgroundSha256':refs['draft'],'references':{'cinematic':refs['draft']}},403)
        payload={'schemaVersion':'ngs.background-animation-workflow.v1','projectId':'binari',**pin,
                 'motionPrompt':'장지문 뒤 그림자가 흔들린다','playback':PLAYBACK}
        parent='request.official.'+'8'*64
        js="""import {PlannerStore} from './workflow-planner/planner.mjs';
import {canonical,sha} from './workflow-planner/envelope.mjs';
const [file,oid,raw]=process.argv.slice(1),payload=JSON.parse(raw),s=new PlannerStore(file,{cinematicEnabled:true});
const payloadSha256=sha(canonical(payload));
s.stage({officialRequestId:oid,projectId:'binari',requestType:'background.animation.workflow',schemaVersion:payload.schemaVersion,
 payload,payloadSha256,registeredAt:1,status:'registered'});s.acknowledge(oid);
s.db.prepare("UPDATE workflow_tasks SET status='running',updated_at=5 WHERE official_request_id=?").run(oid);
console.log(payloadSha256);s.close();"""
        payload_sha=subprocess.run(['node','--input-type=module','-e',js,str(db),parent,json.dumps(payload)],
                         cwd=REPO,check=True,capture_output=True,text=True).stdout.strip()
        for v in selected:
            key='background_animation_'+v
            tid='workflow.task.'+digest([parent,key]);sid='request.official.'+digest([parent,key])
            claim={'officialRequestId':parent,'projectId':'binari','taskId':tid,'payloadSha256':payload_sha,'claimStamp':5}
            inp=call('/read',{'claim':claim})
            self.assertEqual(inp,{'sourceSha256':refs[v],'motionPrompt':payload['motionPrompt'],'playback':PLAYBACK})
            self.assertEqual(call('/asset',{'claim':claim},binary=True),raws[v])
            raw=png(10 if v=='pixel' else 12,256,256);f={'bytes':len(raw),'sha256':sha(raw)}
            for i in range(45):
                call('/frame',{'claim':claim,'frame':{**f,'index':i,'base64':base64.b64encode(raw).decode()}})
            group={'requestId':parent,'variant':v,'backgroundSha256':refs['pixel']}
            m={'schemaVersion':'ngs.cinematic-sequence-result.v1','projectId':'binari','officialRequestId':sid,
               'sourceSha256':refs[v],'group':group,'width':256,'height':256,'masterFps':24,'frameCount':45,
               'upscale':{'model':'seedvr2-7b-fp16','scale':2,'sourceWidth':128,'sourceHeight':128},
               'frames':[{**f,'index':i} for i in range(45)],'playback':PLAYBACK}
            call('',{'claim':claim,'manifest':{**m,'sourceSha256':refs['draft']}},403)
            call('',{'claim':claim,'manifest':{**m,'upscale':{**m['upscale'],'scale':1}}},403)
            if v=='pixel':
                call('',{'claim':claim,'manifest':m},403)
                m['upscale']['pixelCorrection']={'method':'reference-palette-v1','upscaledFramesSha256':'b'*64}
            low={k:v for k,v in m.items() if k!='upscale'}
            call('',{'claim':claim,'manifest':{**low,'width':128,'height':128}},403)
            ref='binari/cinematics/sequences/'+sid+'/manifest.json'
            self.assertEqual(call('',{'claim':claim,'manifest':m}),{'ok':True,'resultRef':ref})
            with sqlite3.connect(db) as conn:conn.execute("UPDATE workflow_tasks SET status='completed',result_ref=? WHERE task_id=?",(ref,tid))
            self.assertEqual(call('/result',{'officialRequestId':sid,'group':group}),{'ok':True,'resultRef':ref,'manifest':m})

    def test_background_has_typed_claim_and_publishes_only_to_background_collection(self):
        root,db,claim,call,payload,_=self.sequence(background=True)
        base=PROJECT_ORIGIN+BACKGROUND
        self.assertEqual(call('',None,base=base),{'schemaVersion':'workflow.background-source-write.v1','enabled':True})
        self.assertEqual(call('/read',{'claim':claim},base=base),
                         {k:v for k,v in payload.items() if k not in ('schemaVersion','projectId')})
        call('/read',{'claim':claim},403,base=PROJECT_ORIGIN+SOURCE)
        raw=png(1,1536,1024)
        artifact={'sha256':sha(raw),'bytes':len(raw),'width':1536,'height':1024,'base64':base64.b64encode(raw).decode()}
        ref='binari/cinematics/backgrounds/'+sha(raw)+'.json'
        self.assertEqual(call('',{'claim':claim,'artifact':artifact},base=base),{'ok':True,'resultRef':ref})
        self.assertEqual((root/'backgrounds'/(sha(raw)+'.png')).read_bytes(),raw)
        self.assertFalse((root/'sources'/(sha(raw)+'.png')).exists())
        self.assertEqual(call('/reconcile',{'claim':claim},base=base),{'state':'published','resultRef':ref})
        with sqlite3.connect(db) as conn:conn.execute("UPDATE workflow_tasks SET status='completed',result_ref=?",(ref,))
        result=call('/result',{'officialRequestId':OID},base=base)
        self.assertEqual(result['resultRef'],ref)
        self.assertEqual(result['manifest']['schemaVersion'],'ngs.background-source-result.v1')

    def test_background_two_independent_pinned_deletes_and_no_resurrection(self):
        from project_data_service.workflow_file_delete import BACKGROUND_ROOT
        from project_data_service.single_target_delete import PATH as DELETE_PATH
        from project_data_service.filex_read import canonical,digest
        root,db,claim,call,_,_=self.sequence(background=True)
        raw=png(1,1536,1024); h=sha(raw)
        artifact={'sha256':h,'bytes':len(raw),'width':1536,'height':1024,'base64':base64.b64encode(raw).decode()}
        generation={'claim':claim,'artifact':artifact}
        base=PROJECT_ORIGIN+BACKGROUND
        call('',generation,base=base)
        self.assertEqual(call('/background',None,base=DELETE_PATH)['roots'],[BACKGROUND_ROOT])
        self.assertEqual(call('',None,base=DELETE_PATH)['roots'],[])
        def offer(label,suffix,source=OID):
            oid='request.official.'+label*64
            tid='workflow.task.'+digest([oid,'file_delete'])
            t={'root':BACKGROUND_ROOT,'path':h+suffix,'kind':'file'}
            inp={'target':t,'expected':{'sourceRequestId':source}}
            p={'schemaVersion':'ngs.file-delete.v1','projectId':'binari',**inp}
            with sqlite3.connect(db) as conn:
                conn.execute("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,1,'planned',1,NULL)",
                             (oid,'binari','file.delete',p['schemaVersion'],canonical(p).decode(),digest(p)))
                conn.execute("INSERT INTO workflow_tasks (task_id,official_request_id,task_key,task_type,worker_type,task_input,depends_on_task_id,updated_at,result_ref,status) VALUES(?,?,'file_delete','file.delete','file',?,NULL,20,NULL,'running')",
                             (tid,oid,canonical(inp).decode()))
            return {'target':t,'claim':{'officialRequestId':oid,'taskId':tid,'projectId':'binari',
                                       'payloadSha256':digest(p),'claimStamp':20}}
        call('',offer('5','.json','request.official.'+'6'*64),409,base=DELETE_PATH)
        image=root/'backgrounds'/(h+'.png'); metadata=root/'backgrounds'/(h+'.json')
        self.assertTrue(image.exists() and metadata.exists())
        meta_delete=offer('7','.json')
        meta_result=call('',meta_delete,base=DELETE_PATH)
        self.assertTrue(meta_result['targetAbsent'])
        self.assertFalse(metadata.exists())
        self.assertEqual(image.read_bytes(),raw)
        self.assertEqual(call('',meta_delete,base=DELETE_PATH),meta_result)
        self.assertTrue(call('',offer('8','.png'),base=DELETE_PATH)['targetAbsent'])
        self.assertFalse(image.exists())
        call('',generation,503,base=base)
        self.assertFalse(metadata.exists() or image.exists())

    def test_source_reconcile_fences_late_publish_and_preserves_already_stored_png(self):
        root,db,claim,call,_,_=self.sequence(source_job=True)
        base=PROJECT_ORIGIN+SOURCE
        raw=png(1,1536,1024)
        artifact={'sha256':sha(raw),'bytes':len(raw),'width':1536,'height':1024,'base64':base64.b64encode(raw).decode()}
        offer={'claim':claim,'artifact':artifact}
        receipt=root/'.source-receipts'/(OID+'.json')
        path=root/'sources'/(artifact['sha256']+'.png')
        ref='binari/cinematics/sources/'+artifact['sha256']+'.json'
        call('/reconcile',{'claim':{**claim,'claimStamp':4}},403,base=base)
        self.assertEqual(call('/reconcile',{'claim':claim},base=base),{'state':'absent_fenced'})
        self.assertEqual(call('/reconcile',{'claim':claim},base=base),{'state':'absent_fenced'})
        call('',offer,503,base=base)
        self.assertFalse(receipt.exists())
        self.assertFalse(path.exists())
        with sqlite3.connect(db) as conn:conn.execute('UPDATE workflow_tasks SET updated_at=6')
        current={**claim,'claimStamp':6}
        self.assertEqual(call('',{**offer,'claim':current},base=base),{'ok':True,'resultRef':ref})
        self.assertEqual(call('/reconcile',{'claim':current},base=base),{'state':'published','resultRef':ref})
        self.assertEqual(path.read_bytes(),raw)
        # An intent with a missing artifact is not a no-write proof.
        path.unlink()
        self.assertEqual(call('/reconcile',{'claim':current},503,base=base),{'error':'WORKFLOW_CINEMATIC_OUTCOME_UNCERTAIN'})
        self.assertTrue(receipt.exists())
        self.assertEqual(call('',{**offer,'claim':current},base=base),{'ok':True,'resultRef':ref})
        self.assertEqual(call('/reconcile',{'claim':current},base=base),{'state':'published','resultRef':ref})
        self.assertEqual(path.read_bytes(),raw)

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
