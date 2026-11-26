"""Prove the formal map path against the unified writer and the real PDS claim guard."""
import base64
import json
import subprocess
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request,urlopen

from media_store import CinematicProjectStore
from project_data_service.filex_cas_transport import FilexCASTransport
from project_data_service.http_server import Handler
from project_data_service.workflow_delete_claim import PrivatePlannerClaim
from project_data_service.workflow_map import WorkflowMap,PATH,CAP
from test_cinematic_sequence_http import REPO,png,sha
from test_scope_registry import UnifiedFixture

class MapWorkflowFixture(UnifiedFixture):
    def test_map_roundtrip_via_real_pds_and_scoped_writer(self):
        store=CinematicProjectStore(self.data/'cinematics')
        regions=[]
        for i in range(1,6):
            raw=png(i,1536,1024)
            digest=sha(raw);request='request.official.'+str(i)*64
            store.cinematic_source_generate({'manifest':{
                'schemaVersion':'ngs.background-source-result.v1','projectId':'binari','officialRequestId':request,
                'title':'테스트 '+str(i)+' · 픽셀 보정','sha256':digest,'bytes':len(raw),'width':1536,'height':1024},
                'base64':base64.b64encode(raw).decode(),'claimStamp':i},background=True)
            regions.append({'id':f'zone-{i}','name':f'Zone {i}','description':'Continue through the opening',
                'backgroundId':'background.'+request[-64:],'imageSha256':digest})
        links=[{'id':f'link-{i}','from':f'zone-{i}','to':f'zone-{i+1}','label':'Continue','hit':None,'spawn':None}
               for i in range(1,5)]
        graph={'title':'Five zones','startRegionId':'zone-1','regions':regions,'connections':links}
        oid='request.official.'+'a'*64
        body={'schemaVersion':'ngs.map-generate.v1','projectId':'binari','graph':graph,'intent':'Preserve the route'}
        db=self.root/'planner.sqlite'
        script="""import {PlannerStore} from './workflow-planner/planner.mjs';
import {canonical,sha} from './workflow-planner/envelope.mjs';
const [db,id,raw]=process.argv.slice(1),payload=JSON.parse(raw),s=new PlannerStore(db,{multiDependencies:true}),h=sha(canonical(payload));
s.stage({officialRequestId:id,requestType:'map.generate',projectId:'binari',schemaVersion:payload.schemaVersion,payload,payloadSha256:h,registeredAt:1,status:'registered'});
s.acknowledge(id);
s.db.prepare("UPDATE workflow_tasks SET status='running',updated_at=5").run();
console.log(JSON.stringify({officialRequestId:id,projectId:'binari',taskId:s.db.prepare('SELECT task_id FROM workflow_tasks').get().task_id,payloadSha256:h,claimStamp:5}));
s.close();"""
        claim=json.loads(subprocess.check_output(['node','--input-type=module','-e',script,str(db),oid,json.dumps(body)],cwd=REPO))
        db.chmod(0o600);st=db.stat()
        planner=PrivatePlannerClaim(str(db),st.st_dev,st.st_ino)
        def factory():
            return FilexCASTransport({'schemaVersion':'pds-filex-cas.v1','projectId':'binari','enabled':True,
                'endpoint':self.origin,'tokenFile':str(self.secret/'media'/'writer_token'),'writerScope':'cinematics'})
        app=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        app.workflow_map=WorkflowMap(planner,writer_factory=factory)
        th=Thread(target=app.serve_forever,daemon=True);th.start()
        def stop():app.shutdown();app.server_close();th.join()
        self.addCleanup(stop)
        def call(tail='',packet=None,status=200):
            req=Request(f'http://127.0.0.1:{app.server_port}'+PATH+tail,data=None if packet is None else json.dumps(packet).encode(),
                headers={} if packet is None else {'Content-Type':'application/json'})
            try:
                with urlopen(req,timeout=5) as response:self.assertEqual(response.status,status);return json.load(response)
            except HTTPError as error:
                with error:self.assertEqual(error.code,status);return json.load(error)
        self.assertEqual(call(),{'enabled':True,'schemaVersion':CAP})
        self.assertEqual([x['id'] for x in call('/read',{'claim':claim})['backgrounds']],[x['backgroundId'] for x in regions])
        h=regions[0]['imageSha256']
        self.assertEqual(sha(base64.b64decode(call('/image',{'claim':claim,'imageSha256':h})['base64'])),h)
        posted={**graph,'connections':[{**link,'hit':{'x':20,'y':750,'width':90,'height':120},'spawn':{'x':450,'y':850}} for link in links]}
        mutated={**posted,'connections':[{**posted['connections'][0],'from':'zone-5'},*posted['connections'][1:]]}
        call('',{'claim':claim,'graph':mutated},403)
        result=call('',{'claim':claim,'graph':posted})
        self.assertEqual(result,{'ok':True,'resultRef':f'binari/cinematics/maps/{oid}/1.json'})
        f=self.data/'cinematics'/'maps'/oid/'1.json'
        self.assertEqual(json.loads(f.read_text())['graph'],posted)
        import sqlite3
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE workflow_tasks SET status='completed',result_ref=?", (result['resultRef'],))
        self.assertEqual(call('/result',{'officialRequestId':oid}),result)
