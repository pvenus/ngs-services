"""Exercise the actual PDS claim route and live-role Filex HTTP batch."""
import base64
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import zlib

from project_data_service.filex_cas_transport import FilexCASTransport
from project_data_service.filex_read import canonical, digest
from project_data_service.http_server import Handler
from unittest.mock import patch
from project_data_service.motion_presets import MotionPresets, PREFIX
from project_data_service.workflow_delete_claim import PrivatePlannerClaim
from project_data_service import workflow_motion_2d as m

ROOT=Path(__file__).resolve().parents[2]
OID='request.official.'+'a'*64
LABEL='걷기 2D'
SOURCE='walk'
TARGET='walk-stylized-2d'


def png(rgb):
    def part(tag,value):
        return struct.pack('!I',len(value))+tag+value+struct.pack('!I',zlib.crc32(tag+value))
    return m.PNG+part(b'IHDR',struct.pack('!IIBBBBB',1,1,8,2,0,0,0))+part(
        b'IDAT',zlib.compress(b'\0'+rgb))+part(b'IEND',b'')


def asset(raw):
    return {'base64':base64.b64encode(raw).decode(),'sha256':m.sha(raw)}


class MotionHttp(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name).resolve()
        outlines=self.root/'filex';outlines.mkdir()
        token=self.root/'test-token';token.write_text('fixture-only'*4+'\n');token.chmod(0o600)
        script='''from server import handler,ThreadingHTTPServer
from motion_store import MotionStore
import pathlib,sys
s=ThreadingHTTPServer(('127.0.0.1',0),handler(MotionStore(sys.argv[1]),pathlib.Path(sys.argv[2]).read_text().strip()))
print(s.server_port,flush=True)
s.serve_forever()
'''
        proc=subprocess.Popen([sys.executable,'-B','-c',script,str(outlines),str(token)],
                  cwd=ROOT/'filex-live-roles/motion-presets',stdout=subprocess.PIPE,text=True)
        def close():
            proc.terminate()
            proc.wait(timeout=5)
            proc.stdout.close()
        self.addCleanup(close)
        port=int(proc.stdout.readline())
        config={'schemaVersion':'pds-filex-cas.v1','projectId':'binari','enabled':True,'tokenFile':str(token),
                'endpoint':f'http://127.0.0.1:{port}'}
        self.motion=MotionPresets(lambda:FilexCASTransport(config))
        self.pdu={'schemaVersion':m.SCHEMA,'projectId':'binari','sourcePresetId':SOURCE,'variantPresetId':TARGET,'label':LABEL}
        self.hash=digest(self.pdu)
        self.db=self.root/'planner.sqlite'
        self.record('''CREATE TABLE workflow_runs(official_request_id TEXT PRIMARY KEY,project_id TEXT,request_type TEXT,
            schema_version TEXT,payload TEXT,payload_sha256 TEXT,state TEXT,reason TEXT,registered_at INTEGER)''')
        self.record('''CREATE TABLE workflow_tasks(task_id TEXT PRIMARY KEY,official_request_id TEXT,task_key TEXT,
            task_type TEXT,worker_type TEXT,task_input TEXT,depends_on_task_id TEXT,status TEXT,result_ref TEXT,updated_at INTEGER)''')
        self.db.chmod(0o600)
        self.record('INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,?,?,?)',
                    (OID,'binari',m.REQUEST,m.SCHEMA,canonical(self.pdu).decode(),self.hash,'planned',None,1000))
        for k in (m.IMAGE,m.FINAL,*[m.task_key(d) for d in m.DIRECTIONS]):
            kind,worker=m.type_for(k)
            self.record('INSERT INTO workflow_tasks VALUES(?,?,?,?,?,?,?,?,?,?)',(m.task_id(OID,k),OID,k,kind,worker,
                canonical(m.expected_input(self.pdu,OID,k)).decode(),m.expected_parent(OID,k),'pending',None,1001))
        self.original={}
        fmt=b'\0\0\0\x18ftypmp42'
        for i,d in enumerate(m.DIRECTIONS):
            self.original[d]=fmt+b'source:'+d.encode()
        self.clip={d:fmt+b'2d:'+d.encode() for d in m.DIRECTIONS}
        self.source={'schemaVersion':'ngs.motion-preset.v1','presetId':SOURCE,'posePreset':'idle','label':'원본 3D',
            'policy':{'motionInstruction':'One stride','framingPolicy':{'schemaVersion':'ngs.animation-motion-safe-frame.v1',
                'maxHeightRatio':.8,'maxWidthRatio':.8,'offsetY':0,'allowUpscale':False},
                'equipmentPolicy':{'heldItems':'','weaponState':'','excludedItems':[]}},
            'directions':[{'direction':d,'assetId':m.sha(raw)+'.mp4','sha256':m.sha(raw),'bytes':len(raw),
                          'frameCount':3,'fps':6,'cycleCount':1,'durationSeconds':.5}
                          for d,raw in self.original.items()]}
        self.seed(self.source,'source-001',self.original)
        self.source_file=outlines/'presets'/f'{SOURCE}.json'
        self.source_bytes=self.source_file.read_bytes()
        s=self.db.stat()
        self.media={'frames':3,'fps':6,'duration':.5,'width':16,'height':16}
        service=m.WorkflowMotion2D(self.motion,PrivatePlannerClaim(str(self.db),s.st_dev,s.st_ino),
                                    self.root/'temp-stage',lambda p:self.media)
        class QuietHandler(Handler):
            def log_message(self,*_):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),QuietHandler)
        server.workflow_motion_2d=service
        thread=threading.Thread(target=server.serve_forever,args=(.01,),daemon=True);thread.start()
        self.port=server.server_port
        self.service=service
        def shut():
            server.shutdown();thread.join(timeout=5);server.server_close()
        self.addCleanup(shut)

    def seed(self,preset,rid,raw,expected=None):
        self.motion.register({'requestId':rid,'expectedHash':expected,'preset':preset,
             'assets':{m.sha(v)+'.mp4':base64.b64encode(v).decode() for v in raw.values()}})

    def record(self,query,args=()):
        with sqlite3.connect(self.db) as c:c.execute(query,args)

    def state(self,k,status,result=None):
        self.record('UPDATE workflow_tasks SET status=?,result_ref=? WHERE task_key=?',(status,result,k))

    def claim(self,k):
        return {'officialRequestId':OID,'taskId':m.task_id(OID,k),'projectId':'binari',
                 'payloadSha256':self.hash,'claimStamp':1001}

    def request(self,body=None,*,read=False,headers=None,status=200):
        c=HTTPConnection('127.0.0.1',self.port,timeout=12)
        try:
            raw=None if body is None else canonical(body)
            c.request('GET' if body is None else 'POST',m.PATH+('/read' if read else ''),body=raw,
                      headers={'Content-Type':'application/json',**(headers or {})})
            reply=c.getresponse();data=json.loads(reply.read())
            self.assertEqual(reply.status,status,data)
            return data
        finally:
            c.close()

    def initialize(self):
        self.state(m.IMAGE,'running')
        self.assertEqual(self.request({'claim':self.claim(m.IMAGE)},read=True),{'sourcePresetHash':digest(self.source)})
        image=png(b'\xff\0\0')
        reply=self.request({'claim':self.claim(m.IMAGE),'artifact':asset(image)})
        self.assertEqual(reply,{'ok':True,'resultRef':'motion.2d.image.'+m.sha(image)})
        self.state(m.IMAGE,'completed',reply['resultRef'])
        return image

    def clips(self,*,omit=None):
        image=self.initialize()
        for d in m.DIRECTIONS:
            if d==omit:continue
            key=m.task_key(d);self.state(key,'running')
            claim=self.claim(key)
            read=self.request({'claim':claim},read=True)
            self.assertEqual(set(read),{'direction','sourcePresetHash','reference','image'})
            self.assertEqual(read['direction'],d)
            self.assertEqual(read['sourcePresetHash'],digest(self.source))
            self.assertEqual(base64.b64decode(read['reference']['base64']),self.original[d])
            self.assertEqual(read['image'],{**asset(image),'bytes':len(image)})
            ref=self.request({'claim':claim,'artifact':asset(self.clip[d])})['resultRef']
            self.assertEqual(ref,'motion.2d.clip.'+d+'.'+m.sha(self.clip[d]))
            self.state(key,'completed',ref)

    def test_actual_http_single_image_eight_clips_atomic_distinct_publication_and_replay(self):
        self.assertEqual(self.request(),{'enabled':True,'schemaVersion':'workflow.motion-preset-convert-2d.v1'})
        self.clips()
        self.assertEqual(len(self.motion.list()['presets']),1)
        self.state(m.FINAL,'running')
        body={'claim':self.claim(m.FINAL),'artifact':{}}
        result=self.request(body)
        self.assertEqual(self.request(body),result)
        found={x['presetId']:x for x in self.motion.list()['presets']}
        self.assertEqual(set(found),{SOURCE,TARGET})
        new=found[TARGET]
        self.assertEqual(result['resultRef'],'motion.2d.published.'+new['presetHash'])
        self.assertEqual(new['variant'],{'kind':'2d','sourcePresetId':SOURCE,'sourcePresetHash':digest(self.source)})
        self.assertEqual(new['label'],LABEL)
        self.assertEqual(self.source_file.read_bytes(),self.source_bytes)
        for row in new['directions']:
            d=row['direction']
            self.assertEqual((row['frameCount'],row['fps'],row['cycleCount'],row['durationSeconds']),(3,6,1,.5))
            self.assertEqual(base64.b64decode(self.motion.get(TARGET,d)['base64']),self.clip[d])
            self.assertEqual(base64.b64decode(self.motion.get(SOURCE,d)['base64']),self.original[d])
        with self.motion.factory() as t:
            rec=t.receipt(OID)
            self.assertEqual(rec['hashes'],{PREFIX+'presets/'+TARGET+'.json':new['presetHash']})
        with self.assertRaisesRegex(ValueError,'MOTION_PRESET_VARIANT_WORKFLOW_REQUIRED'):
            self.seed({**self.source,'presetId':TARGET},'unsafe-002',self.original,expected=new['presetHash'])

    def test_fixed_startup_discovers_existing_ports_only(self):
        fixture=self.root/'motion-config.json'
        fixture.write_bytes(canonical({'projectId':'binari','schemaVersion':'pds-filex-cas.v1',
              'enabled':True,'endpoint':'http://127.0.0.1:8096','tokenFile':str(self.root/'test-token')}))
        fixture.chmod(0o600)
        probe=self.root/'ffprobe'
        probe.write_text('not executed by startup')
        probe.chmod(0o500)
        args=(self.service.verifier,self.root,'127.0.0.1')
        with patch.object(m,'CONFIG',fixture),patch.object(m,'FFPROBE',probe):
            self.assertIsInstance(m.configured(*args,'auto'),m.WorkflowMotion2D)
            self.assertIsNone(m.configured(*args,'0'))
            self.assertIsNone(m.configured(None,self.root,'127.0.0.1','auto'))
            self.assertIsNone(m.configured(self.service.verifier,self.root,'0.0.0.0','auto'))
            probe.chmod(0o522)
            self.assertIsNone(m.configured(*args,'auto'))
            with self.assertRaisesRegex(ValueError,'MOTION_2D_EXISTING_PORTS_UNAVAILABLE'):
                m.configured(*args,'1')
        self.assertFalse((self.root/'workflow-motion-convert-2d').exists())

    def test_source_change_in_filex_batch_window_rejects_with_neither_variant_nor_receipt(self):
        self.clips()
        self.state(m.FINAL,'running')
        body={'claim':self.claim(m.FINAL),'artifact':{}}
        original_factory=self.motion.factory
        moved=False
        class Interleaved:
            def __enter__(outer):
                outer.writer=original_factory().__enter__()
                return outer
            def __exit__(outer,*args):
                return outer.writer.__exit__(*args)
            def __getattr__(outer,name):
                return getattr(outer.writer,name)
            def call(outer,method,path,body=None,**kwargs):
                nonlocal moved
                if method=='POST' and path=='/v1/batch' and body['requestId']==OID and not moved:
                    moved=True
                    competing=MotionPresets(original_factory)
                    competing.register({'requestId':'competing-1','expectedHash':digest(self.source),
                       'preset':{**self.source,'label':'changed during CAS'},'assets':
                       {m.sha(v)+'.mp4':base64.b64encode(v).decode() for v in self.original.values()}})
                return outer.writer.call(method,path,body,**kwargs)
        self.motion.factory=Interleaved
        self.request(body,status=409)
        self.assertTrue(moved)
        self.assertEqual([x['presetId'] for x in self.motion.list()['presets']],[SOURCE])
        with original_factory() as t:self.assertIsNone(t.receipt(OID))

    def final_upload_interleaving(self,mutate,expected_status):
        self.clips()
        self.state(m.FINAL,'running')
        original_factory=self.motion.factory
        blocked=threading.Event()
        resume=threading.Event()
        self.addCleanup(resume.set)
        cas_timeouts=[]
        class SlowTransfer:
            def __enter__(outer):
                outer.writer=original_factory().__enter__()
                return outer
            def __exit__(outer,*args):
                return outer.writer.__exit__(*args)
            def __getattr__(outer,name):
                return getattr(outer.writer,name)
            def call(outer,method,path,body=None,**kwargs):
                if method=='POST' and path=='/v1/asset' and not blocked.is_set():
                    blocked.set()
                    if not resume.wait(3):
                        raise RuntimeError('fixture transfer was not resumed')
                if method=='POST' and path=='/v1/batch':
                    cas_timeouts.append(kwargs.get('timeout'))
                return outer.writer.call(method,path,body,**kwargs)
        self.motion.factory=SlowTransfer
        body={'claim':self.claim(m.FINAL),'artifact':{}}
        with ThreadPoolExecutor(max_workers=1) as child:
            pending=child.submit(self.request,body,status=expected_status)
            try:
                self.assertTrue(blocked.wait(2))
                # The transfer cannot continue until AFTER an independent
                # rollback-journal writer has completed a real commit.
                with sqlite3.connect(self.db,timeout=.2) as other:
                    other.execute('BEGIN IMMEDIATE')
                    mutate(other)
                self.assertFalse(pending.done())
            finally:
                resume.set()
            return pending.result(timeout=5),cas_timeouts

    def test_slow_filex_upload_does_not_hold_planner_reader(self):
        unrelated='request.official.'+'b'*64
        self.record('INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,?,?,?)',
          (unrelated,'binari','unrelated','example.v1','{}','1'*64,'planned',None,1000))
        result,timeouts=self.final_upload_interleaving(lambda db:db.execute(
          "UPDATE workflow_runs SET state='completed' WHERE official_request_id=?",(unrelated,)),200)
        self.assertTrue(result['ok'])
        self.assertEqual(timeouts,[1.5])
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT state FROM workflow_runs WHERE official_request_id=?',
                                         (unrelated,)).fetchone(),('completed',))

    def test_finalizer_claim_lost_during_upload_never_reaches_filex_cas(self):
        result,timeouts=self.final_upload_interleaving(lambda db:db.execute(
          "UPDATE workflow_tasks SET updated_at=updated_at+1 WHERE task_key=?",(m.FINAL,)),409)
        self.assertEqual(result,{'error':'MOTION_2D_OUTCOME_UNCONFIRMED'})
        self.assertEqual(timeouts,[])
        self.assertEqual([p['presetId'] for p in self.motion.list()['presets']],[SOURCE])
        with self.motion.factory() as t:
            self.assertIsNone(t.receipt(OID))

    def test_stale_clip_during_slow_probe_never_commits_local_intermediate(self):
        self.initialize()
        k=m.task_key('ne');self.state(k,'running')
        def lost_during_decoding(_):
            with sqlite3.connect(self.db,timeout=.2) as other:
                other.execute('UPDATE workflow_tasks SET updated_at=updated_at+1 WHERE task_key=?',(k,))
            return self.media
        self.service.probe=lost_during_decoding
        self.request({'claim':self.claim(k),'artifact':asset(self.clip['ne'])},status=422)
        self.assertFalse((self.root/'temp-stage'/OID/'ne.mp4').exists())
        self.assertEqual([p['presetId'] for p in self.motion.list()['presets']],[SOURCE])

    def test_only_output_is_bounded_to_eight_mib(self):
        data=b'x'*(8*1024*1024)
        self.assertEqual(m.artifact_bytes(asset(data),m.MAX_OUTPUT),data)
        with self.assertRaisesRegex(ValueError,'MOTION_2D_REJECTED'):
            m.artifact_bytes(asset(data+b'x'),m.MAX_OUTPUT)
        self.assertEqual(m.MAX_REFERENCE,64*1024*1024)

    def test_reject_stale_claim_foreign_origin_bad_png_and_conflicting_result(self):
        self.state(m.IMAGE,'running')
        claim=self.claim(m.IMAGE)
        self.request({'claim':{**claim,'claimStamp':1000}},read=True,status=422)
        self.request({'claim':claim},read=True,headers={'Origin':'http://example.invalid'},status=403)
        self.request({'claim':claim},read=True)
        self.request({'claim':claim,'artifact':asset(b'spoof bytes')},status=422)
        item=asset(png(b'\1\2\3'))
        self.request({'claim':claim,'artifact':item})
        self.request({'claim':claim,'artifact':asset(png(b'\2\3\4'))},status=422)
        self.assertEqual(len(self.motion.list()['presets']),1)

    def test_reject_changed_timing_missing_or_tampered_dependency_without_catalog_change(self):
        self.clips(omit='nw')
        self.state(m.FINAL,'running')
        body={'claim':self.claim(m.FINAL),'artifact':{}}
        self.request(body,status=422)
        key=m.task_key('nw');self.state(key,'running')
        art={'claim':self.claim(key),'artifact':asset(self.clip['nw'])}
        self.media={**self.media,'frames':2}
        self.request(art,status=422)
        self.media={**self.media,'frames':3}
        res=self.request(art)
        self.state(key,'completed',res['resultRef'])
        self.state(m.task_key('w'),'completed','motion.2d.clip.w.'+'1'*64)
        self.request(body,status=422)
        self.assertEqual(len(self.motion.list()['presets']),1)

    def test_reject_source_change_after_basis_and_concurrent_existing_target(self):
        self.clips()
        self.state(m.FINAL,'running')
        body={'claim':self.claim(m.FINAL),'artifact':{}}
        foreign={**self.source,'presetId':TARGET,'label':'already occupied'}
        self.seed(foreign,'external-002',self.original)
        self.request(body,status=409)
        self.assertNotIn('variant',next(x for x in self.motion.list()['presets'] if x['presetId']==TARGET))
        switched={**self.source,'label':'changed 3D source'}
        self.seed(switched,'external-003',self.original,expected=digest(self.source))
        self.request(body,status=422)


class MediaProbe(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ffprobe') and shutil.which('ffmpeg'),'ffprobe/ffmpeg needed for real media metadata fixture')
    def test_real_mp4_timing_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'valid.mp4'
            subprocess.run([shutil.which('ffmpeg'),'-v','error','-f','lavfi','-i','color=c=red:s=16x16:r=6',
                             '-frames:v','3','-c:v','mpeg4',str(file)],check=True,capture_output=True)
            meta=m.FFProbe(shutil.which('ffprobe'))(file)
            self.assertEqual(meta,{'frames':3,'fps':6.,'duration':.5,'width':16,'height':16})
            self.assertEqual(m.check_clip_timing(meta,{'frameCount':3,'fps':6,'durationSeconds':.5}),
                             {'width':16,'height':16})
