"""Exercise real PlannerStore/PDS/Filex on disposable SQLite and scopes."""
import base64
import hashlib
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import struct
import subprocess
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request,urlopen
import zlib

from project_data_service.filex_cas_transport import FilexCASTransport
from project_data_service.http_server import Handler
from project_data_service.workflow_delete_claim import PrivatePlannerClaim
from project_data_service.workflow_ui_asset import BASE, DESIGN, STAGED, WorkflowUiAsset
from test_scope_registry import UnifiedFixture

REPO=Path(__file__).resolve().parents[1]
MARKDOWN='# UI 디자인\n\n먹글씨와 한지 질감의 단일 게임 UI.'

def png(width=8,height=3,color=6):
    header=struct.pack('>IIBBBBB',width,height,8,color,0,0,0)
    body=(b'\0'+bytes((123,90,50,0) if color==6 else (123,90,50))*width)*height
    def chunk(name,data):return struct.pack('>I',len(data))+name+data+struct.pack('>I',zlib.crc32(name+data))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',header)+chunk(b'IDAT',zlib.compress(body))+chunk(b'IEND',b'')
def sha(data):return hashlib.sha256(data).hexdigest()
def blob(raw):return {'sha256':sha(raw),'base64':base64.b64encode(raw).decode()}


class UiAssetHttp(UnifiedFixture):
    def build(self,mode):
        self.data_db=self.root/'planner.sqlite'
        db=self.data_db
        self.oid='request.official.'+('a' if mode=='generate' else 'b')*64
        self.raw=png()
        values={'schemaVersion':'ngs.ui-asset-'+mode+'.v1','projectId':'binari','kind':'button','title':'들어가기'}
        values.update({'prompt':'넓은 가로형 한지 버튼'} if mode=='generate' else {'sha256':sha(self.raw)})
        js="""
import {PlannerStore} from './workflow-planner/planner.mjs';
import {canonical,sha} from './workflow-planner/envelope.mjs';
const [file,oid,mode,raw]=process.argv.slice(1),payload=JSON.parse(raw),s=new PlannerStore(file);
const payloadSha256=sha(canonical(payload));
s.stage({officialRequestId:oid,projectId:'binari',requestType:'ui.asset.'+mode,schemaVersion:payload.schemaVersion,
 payload,payloadSha256,registeredAt:1,status:'registered'});s.acknowledge(oid);
const taskId=s.db.prepare('SELECT task_id FROM workflow_tasks').get().task_id;
s.db.prepare("UPDATE workflow_tasks SET status='running',updated_at=5").run();
console.log(JSON.stringify({officialRequestId:oid,projectId:'binari',taskId,payloadSha256,claimStamp:5}));
s.close();
"""
        self.claim=json.loads(subprocess.run(['node','--input-type=module','-e',js,str(db),self.oid,mode,json.dumps(values)],
                         cwd=REPO,check=True,capture_output=True).stdout)
        db.chmod(0o600);st=db.stat()
        planner=PrivatePlannerClaim(str(db),st.st_dev,st.st_ino)
        writer={'schemaVersion':'pds-filex-cas.v1','projectId':'binari','enabled':True,'endpoint':self.origin,
                'tokenFile':str(self.secret/'media'/'writer_token'),'writerScope':'ui'}
        self.concept={'document':{'projectId':'ProjectBS','sections':[{'id':DESIGN,'title':'UI 디자인',
                       'fields':[{'id':'content','value':MARKDOWN}]}],
                       'hierarchy':{'groups':[{'id':'group.art-design','categoryIds':[DESIGN]}],'archivedCategoryIds':[]}}}
        self.pds=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        for k in ('generate','import'):
            setattr(self.pds,'workflow_ui_'+k,WorkflowUiAsset(planner,k,lambda:self.concept,
                writer_factory=lambda:FilexCASTransport(writer),stage_dir=self.root/'stage'))
        t=Thread(target=self.pds.serve_forever,daemon=True);t.start()
        def stop():self.pds.shutdown();self.pds.server_close();t.join()
        self.addCleanup(stop)
        return BASE+mode
    def pds_call(self,path,body=None,status=200,extra=None):
        q=Request(f'http://127.0.0.1:{self.pds.server_port}'+path,
                  data=None if body is None else json.dumps(body).encode(),
                  headers={**({} if body is None else {'Content-Type':'application/json'}),**(extra or {})})
        try:
            with urlopen(q,timeout=10) as r:self.assertEqual(r.status,status);return json.load(r)
        except HTTPError as e:
            with e:self.assertEqual(e.code,status);return json.load(e)
    def verify_result(self,route):
        ref='binari/ui/assets/'+self.oid+'/manifest.json'
        receipt=self.pds_call(route+'/result',{'officialRequestId':self.oid})
        self.assertEqual(receipt['resultRef'],ref)
        package=self.data/'ui/assets'/self.oid
        self.assertEqual((package/'asset.png').read_bytes(),self.raw)
        m=receipt['manifest']
        self.assertEqual((m['width'],m['height'],m['sha256']), (8,3,sha(self.raw)))
        self.assertEqual(json.loads((package/'manifest.json').read_bytes()),m)
        with sqlite3.connect(self.data_db) as c:
            self.assertEqual(c.execute('select status,result_ref,save_started_at from workflow_tasks').fetchone(),('completed',ref,None))
        js="""
import {PlannerStore} from './workflow-planner/planner.mjs';
const [file,proof]=process.argv.slice(1);
const store=new PlannerStore(file,{readCinematicPublication:async()=>JSON.parse(proof)});
const candidates=store.terminalReady();
console.log(JSON.stringify({ready:candidates.length===1&&await store.verifyCompletion(candidates[0])}));
store.close();
"""
        terminal=json.loads(subprocess.run(['node','--input-type=module','-e',js,str(self.data_db),json.dumps(receipt)],
            cwd=REPO,check=True,capture_output=True).stdout)
        self.assertTrue(terminal['ready'])
        return m
    def test_actual_manual_bytes_claim_and_ownership(self):
        route=self.build('import')
        self.pds_call(STAGED,blob(self.raw),403,{'Origin':'https://wrong.example'})
        self.pds_call(STAGED,blob(png(color=2)),403)
        self.assertEqual(self.pds_call(STAGED,blob(self.raw)),{'ok':True,'sha256':sha(self.raw)})
        self.assertEqual(self.pds_call(route)['schemaVersion'],'workflow.ui-asset-import.v1')
        self.pds_call(route,{'claim':{**self.claim,'claimStamp':4}},403)
        self.assertTrue(self.pds_call(route,{'claim':self.claim})['ok'])
        m=self.verify_result(route)
        self.assertEqual((m['origin'],m['designSha256']),('manual',None))
    def test_generation_must_use_registered_published_design(self):
        route=self.build('generate')
        self.assertEqual(self.pds_call(route)['schemaVersion'],'workflow.ui-asset-generate.v1')
        e=self.pds_call(route+'/read',{'claim':self.claim})
        proof=sha(MARKDOWN.encode())
        self.assertEqual(e['design'],{'categoryId':DESIGN,'markdown':MARKDOWN,'sha256':proof})
        self.pds_call(route,{'claim':self.claim,'artifact':{**blob(self.raw),'designSha256':'0'*64}},403)
        self.assertTrue(self.pds_call(route,{'claim':self.claim,'artifact':{**blob(self.raw),'designSha256':proof}})['ok'])
        m=self.verify_result(route)
        self.assertEqual((m['origin'],m['designSha256']),('imagegen',proof))
