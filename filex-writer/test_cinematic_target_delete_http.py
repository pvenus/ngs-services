import base64
import json
from http.server import ThreadingHTTPServer
from threading import Thread
import subprocess
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request,urlopen

from media_store import CinematicProjectStore,wire,ARCHIVE_SCHEMA
from project_data_service.filex_cas_transport import FilexCASTransport
from project_data_service.http_server import Handler
from project_data_service.single_target_delete import (
    SingleTargetDelete, PATH, SCHEMA, CINEMATIC_CAPABILITY, CINEMATIC_INVENTORY,
)
from project_data_service.workflow_file_delete import (
    CINEMATIC_ROOT,cinematic_inventory,cinematic_storage_provider,
)
from project_data_service.workflow_delete_claim import PrivatePlannerClaim
from test_cinematic_sequence_http import REPO,OID,png,sha,PLAYBACK
from test_scope_registry import UnifiedFixture


def oid(c):return 'request.official.'+c*64


class CinematicTargetDeleteFixture(UnifiedFixture):
    def setup_scope(self):
        root=self.data/'cinematics';store=CinematicProjectStore(root)
        raw=png(1,1536,1024)
        self.source=sha(raw)
        store.cinematic_source_generate({'manifest':{'schemaVersion':'ngs.cinematic-source-result.v1',
            'projectId':'binari','officialRequestId':OID,'title':'삭제용 장면',
            'sha256':self.source,'bytes':len(raw),'width':1536,'height':1024},
            'base64':base64.b64encode(raw).decode(),'claimStamp':1})
        self.store=store
        self.root_scope=root
        db=self.root/'planner.sqlite'
        self.db=db
        self.js="""import {PlannerStore} from './workflow-planner/planner.mjs';
import {canonical,sha} from './workflow-planner/envelope.mjs';
const [db,id,type,raw,status,reason]=process.argv.slice(1),payload=JSON.parse(raw),store=new PlannerStore(db,{cinematicEnabled:true});
const payloadSha256=sha(canonical(payload));
store.stage({officialRequestId:id,projectId:'binari',requestType:type,schemaVersion:payload.schemaVersion,payload,payloadSha256,registeredAt:1,status:'registered'});
store.acknowledge(id);
store.db.prepare('UPDATE workflow_tasks SET status=?,updated_at=? WHERE official_request_id=?').run(status,5,id);
if(reason)store.db.prepare('UPDATE workflow_runs SET reason=? WHERE official_request_id=?').run(reason,id);
const taskId=store.db.prepare('SELECT task_id FROM workflow_tasks WHERE official_request_id=?').get(id).task_id;
console.log(JSON.stringify({officialRequestId:id,projectId:'binari',taskId,payloadSha256,claimStamp:5}));
store.close();"""
        self.stage(oid('0'),f'sources/{self.source}.png')
        db.chmod(0o600);st=db.stat()
        planner=PrivatePlannerClaim(str(db),st.st_dev,st.st_ino)
        factory=lambda:FilexCASTransport({'schemaVersion':'pds-filex-cas.v1','projectId':'binari','enabled':True,
            'endpoint':self.origin,'tokenFile':str(self.secret/'media'/'writer_token'),'writerScope':'cinematics'})
        def ready():
            with factory() as client:return client.capabilities().get('workflowPinnedCinematicTargetDeleteV1') is True
        app=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        app.single_target_delete=SingleTargetDelete({CINEMATIC_ROOT:cinematic_storage_provider(planner,factory,ready)})
        app.cinematic_delete_inventory=lambda source:cinematic_inventory(planner,factory,source)
        th=Thread(target=app.serve_forever,daemon=True);th.start()
        def stop():app.shutdown();app.server_close();th.join()
        self.addCleanup(stop);self.origin_pds=f'http://127.0.0.1:{app.server_port}'

    def planner_claim(self,id,typ,payload,status='running'):
        return json.loads(subprocess.run(['node','--input-type=module','-e',self.js,str(self.db),id,typ,json.dumps(payload),status,''],
            cwd=REPO,check=True,capture_output=True).stdout)

    def stage(self,id,path,source=None):
        source=source or self.source
        target={'root':CINEMATIC_ROOT,'path':path,'kind':'directory' if path.startswith('sequences/') else 'file'}
        claim=self.planner_claim(id,'file.delete',{'schemaVersion':'ngs.file-delete.v1','projectId':'binari',
            'target':target,'expected':{'sourceSha256':source}})
        return {'target':target,'claim':claim}

    def pds_call(self,path,body=None,status=200):
        req=Request(self.origin_pds+path,data=None if body is None else json.dumps(body).encode(),
            headers={} if body is None else {'Content-Type':'application/json'})
        try:
            with urlopen(req,timeout=10) as r:self.assertEqual(r.status,status);return json.load(r)
        except HTTPError as e:
            with e:self.assertEqual(e.code,status);return json.load(e)

    def seq(self,source,sequenceId):
        frames=[{'index':i,'bytes':1,'sha256':'a'*64} for i in range(45)]
        path=self.root_scope/'sequences'/sequenceId
        self.store.write(path/'manifest.json',wire({'schemaVersion':'ngs.cinematic-sequence-result.v1','projectId':'binari',
            'officialRequestId':sequenceId,'sourceSha256':source,'width':128,'height':128,'frameCount':45,'masterFps':24,'frames':frames,'playback':PLAYBACK}))
        self.store.write(path/'frames'/'0000.png',png(1))
        return path

    def test_archived_sequence_and_source_have_independent_exact_recoverable_moves(self):
        self.setup_scope()
        self.assertEqual(self.pds_call(PATH),{'enabled':False,'schemaVersion':SCHEMA,'roots':[]})
        self.assertEqual(self.pds_call(CINEMATIC_CAPABILITY),{'enabled':True,'schemaVersion':SCHEMA,'roots':[CINEMATIC_ROOT]})
        child=oid('a');target_child=self.seq(self.source,child)
        unrelated=self.seq('b'*64,oid('b'))
        self.store.write(self.root_scope/'sequence-archive.json',wire({'schemaVersion':ARCHIVE_SCHEMA,'projectId':'binari','sequenceIds':[child]}))
        inventory=lambda:self.pds_call(CINEMATIC_INVENTORY+'?'+urlencode({'sourceSha256':self.source}))
        self.assertEqual([t['path'] for t in inventory()['targets']],
            [f'sequences/{child}',f'sources/{self.source}.json',f'sources/{self.source}.png'])
        json_request=self.stage(oid('1'),f'sources/{self.source}.json')
        png_request=self.stage(oid('2'),f'sources/{self.source}.png')
        child_request=self.stage(oid('3'),f'sequences/{child}')
        wrong_request=self.stage(oid('4'),f'sequences/{child}',source='b'*64)
        self.assertEqual(self.pds_call(PATH,json_request,409)['error'],'STORAGE_DELETE_STALE_TARGET')
        self.assertEqual(self.pds_call(PATH,wrong_request,409)['error'],'STORAGE_DELETE_STALE_TARGET')
        self.assertTrue(target_child.exists())
        receipt=self.pds_call(PATH,child_request)
        self.assertEqual(self.pds_call(PATH,child_request),receipt)
        self.assertFalse(target_child.exists())
        self.assertTrue(unrelated.exists())
        self.pds_call(PATH,json_request)
        self.pds_call(PATH,png_request)
        self.assertFalse((self.root_scope/'sources'/(self.source+'.png')).exists())
        self.assertFalse((self.root_scope/'sources'/(self.source+'.json')).exists())
        self.assertTrue((self.root_scope/'sequence-archive.json').exists())
        # Reads and late publishes cannot recreate a retired visibility path.
        from server import Conflict
        with self.assertRaises(Conflict):self.store.cinematic_source({'sourceSha256':self.source})

    def test_pending_generation_protects_source_and_disables_inventory(self):
        self.setup_scope()
        self.planner_claim(oid('5'),'cinematic.sequence.generate',{'schemaVersion':'ngs.cinematic-sequence-generate.v1',
            'projectId':'binari','sourceSha256':self.source,'motionPrompt':'motion','playback':PLAYBACK},status='pending')
        self.pds_call(CINEMATIC_INVENTORY+'?sourceSha256='+self.source,status=409)
        delete=self.stage(oid('1'),f'sources/{self.source}.json')
        self.assertEqual(self.pds_call(PATH,delete,status=403)['error'],'STORAGE_DELETE_CLAIM_DENIED')
        self.assertTrue((self.root_scope/'sources'/(self.source+'.json')).exists())
