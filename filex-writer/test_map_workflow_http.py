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
from project_data_service.single_target_delete import PATH as DELETE,MAP_CAPABILITY,SCHEMA,SingleTargetDelete
from project_data_service.workflow_file_delete import MAP_ROOT,storage_provider
from map_store import encode,map_revision
from server import Conflict
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
const [db,id,raw,requestType]=process.argv.slice(1),payload=JSON.parse(raw),s=new PlannerStore(db,{multiDependencies:true}),h=sha(canonical(payload));
s.stage({officialRequestId:id,requestType,projectId:'binari',schemaVersion:payload.schemaVersion,payload,payloadSha256:h,registeredAt:1,status:'registered'});
s.acknowledge(id);
s.db.prepare("UPDATE workflow_tasks SET status='running',updated_at=5 WHERE official_request_id=?").run(id);
console.log(JSON.stringify({officialRequestId:id,projectId:'binari',taskId:s.db.prepare('SELECT task_id FROM workflow_tasks WHERE official_request_id=?').get(id).task_id,payloadSha256:h,claimStamp:5}));
s.close();"""
        def stage(request_id,payload,request_type):
            return json.loads(subprocess.check_output(['node','--input-type=module','-e',script,str(db),request_id,
                json.dumps(payload),request_type],cwd=REPO))
        claim=stage(oid,body,'map.generate')
        db.chmod(0o600);st=db.stat()
        planner=PrivatePlannerClaim(str(db),st.st_dev,st.st_ino)
        def factory():
            return FilexCASTransport({'schemaVersion':'pds-filex-cas.v1','projectId':'binari','enabled':True,
                'endpoint':self.origin,'tokenFile':str(self.secret/'media'/'writer_token'),'writerScope':'cinematics'})
        app=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        app.workflow_map=WorkflowMap(planner,writer_factory=factory)
        def ready():
            with factory() as conn:return conn.capabilities().get('workflowPinnedMapFolderDeleteV1') is True
        app.single_target_delete=SingleTargetDelete(
            {MAP_ROOT:storage_provider(planner,factory,ready,root=MAP_ROOT)},dedicated=[MAP_ROOT])
        th=Thread(target=app.serve_forever,daemon=True);th.start()
        def stop():app.shutdown();app.server_close();th.join()
        self.addCleanup(stop)
        def call(tail='',packet=None,status=200,prefix=PATH):
            req=Request(f'http://127.0.0.1:{app.server_port}'+prefix+tail,data=None if packet is None else json.dumps(packet).encode(),
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
        manifest=json.loads(f.read_text())
        self.assertEqual(manifest['schemaVersion'],'ngs.map-group.v1')
        self.assertEqual(set(manifest),{'schemaVersion','projectId','mapId','revision','updatedAt','officialRequestId','title','regions','parts'})
        for name,value in {'start':{'startRegionId':'zone-1'},'portals':{'connections':posted['connections']},
                           'collisions':{}}.items():
            raw=f.with_name(f'1.{name}.json').read_bytes()
            self.assertEqual(raw,encode(value))
            self.assertEqual(manifest['parts'][name],sha(raw))
        state=map_revision(store,{'mapId':oid,'revision':1})
        self.assertEqual(state['graph'],posted)
        import sqlite3
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE workflow_tasks SET status='completed',result_ref=?", (result['resultRef'],))
        self.assertEqual(call('/result',{'officialRequestId':oid}),result)

        original=f.read_bytes()
        box={'id':'wall-1','regionId':'zone-2','rect':{'x':10,'y':20,'width':130,'height':300}}
        round_wall={**box,'id':'round-1','shape':'ellipse','rotation':45}
        changed={**posted,'colliders':[box,round_wall]}
        update_id='request.official.'+'b'*64
        update_claim=stage(update_id,{'schemaVersion':'ngs.map-update.v1','projectId':'binari','mapId':oid,
            'expectedRevision':1,'graph':changed},'map.update')
        call('',{'claim':update_claim,'graph':posted},403)
        call('',{'claim':update_claim,'graph':{**changed,'colliders':[box,box]}},403)
        update_result=call('',{'claim':update_claim,'graph':changed})
        self.assertEqual(update_result,{'ok':True,'resultRef':f'binari/cinematics/maps/{oid}/2.json'})
        self.assertEqual(f.read_bytes(),original)
        self.assertEqual(map_revision(store,{'mapId':oid,'revision':2})['graph'],changed)
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE workflow_tasks SET status='completed',result_ref=? WHERE official_request_id=?",
                (update_result['resultRef'],update_id))
        self.assertEqual(call('/result',{'officialRequestId':update_id}),update_result)

        # v2 is a separate three-job DAG and not a synonym for legacy v1.
        split_id='request.official.'+'9'*64
        staged={**body,'schemaVersion':'ngs.map-generate.v2','design':{'startNotes':'Match the gate',
                'portalNotes':'','collisionNotes':'','collisionMode':'none'}}
        claim2=stage(split_id,staged,'map.generate')
        from project_data_service.filex_read import canonical,digest
        with sqlite3.connect(db) as conn:
            rows=conn.execute("SELECT task_key,task_id,depends_on_task_id FROM workflow_tasks WHERE official_request_id=?",
                (split_id,)).fetchall()
        dag={k:(tid,dep) for k,tid,dep in rows}
        self.assertEqual(set(dag),{'map_start','map_portals','map_collisions'})
        a,b,c=[dag[k][0] for k in ('map_start','map_portals','map_collisions')]
        self.assertEqual([dag[k][1] for k in ('map_start','map_portals','map_collisions')],[None,a,b])
        def claim_at(tid):
            return {**claim2,'taskId':tid}
        def complete(tid,out):
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE workflow_tasks SET status='completed',result_ref=?,map_part_json=? WHERE task_id=?",
                    ('workflow.map-stage.'+digest(out),canonical(out).decode(),tid))
        call('/read',{'claim':claim_at(a)})
        call('',{'claim':claim_at(a),'graph':{**posted,'colliders':[]}},403)
        call('/read',{'claim':claim_at(b)},403)
        complete(a,{'startRegionId':'zone-1'})
        call('/read',{'claim':claim_at(b)})
        call('/read',{'claim':claim_at(c)},403)
        coordinates={'coordinates':[{k:e[k] for k in ('id','hit','spawn')} for e in posted['connections']]}
        complete(b,coordinates)
        call('/read',{'claim':claim_at(c)})
        call('',{'claim':claim_at(c),'graph':changed},403) # collisionMode none
        wrong_portal={**posted,'colliders':[],'connections':[{**posted['connections'][0],'spawn':{'x':3,'y':4}},*posted['connections'][1:]]}
        call('',{'claim':claim_at(c),'graph':wrong_portal},403)
        valid={**posted,'colliders':[]}
        expected_split=call('',{'claim':claim_at(c),'graph':valid})
        self.assertEqual(expected_split,{'ok':True,'resultRef':f'binari/cinematics/maps/{split_id}/1.json'})
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE workflow_tasks SET status='completed',result_ref=? WHERE task_id=?",
                (expected_split['resultRef'],c))
        self.assertEqual(call('/result',{'officialRequestId':split_id}),expected_split)
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE workflow_tasks SET map_part_json='{}' WHERE task_id=?",(b,))
        call('/result',{'officialRequestId':split_id},403)

        self.assertEqual(call(prefix=MAP_CAPABILITY),{'enabled':True,'schemaVersion':SCHEMA,'roots':[MAP_ROOT]})
        self.assertEqual(call(prefix=DELETE)['roots'],[],'dedicated map root is absent from generic readiness')
        survivor='request.official.'+'e'*64
        other={**state,'mapId':survivor,'officialRequestId':survivor}
        map_revision(store,other,True)
        bgdir=self.data/'cinematics'/'backgrounds'
        before_backgrounds=sorted(p.name for p in bgdir.iterdir())
        target={'root':MAP_ROOT,'path':oid,'kind':'directory'}
        def delete_claim(ch,revision,publisher):
            return stage('request.official.'+ch*64,{'schemaVersion':'ngs.file-delete.v1','projectId':'binari',
                'target':target,'expected':{'revision':revision,'officialRequestId':publisher}},'file.delete')
        stale=delete_claim('c',1,oid)
        self.assertEqual(call(packet={'claim':stale,'target':target},status=409,prefix=DELETE),
            {'error':'STORAGE_DELETE_STALE_TARGET'})
        wrong_publisher=delete_claim('f',2,oid)
        self.assertEqual(call(packet={'claim':wrong_publisher,'target':target},status=409,prefix=DELETE),
            {'error':'STORAGE_DELETE_STALE_TARGET'})
        self.assertTrue(f.exists())
        self.assertFalse((self.data/'cinematics'/'.map-deleted').exists())
        claim=delete_claim('d',2,update_id)
        self.assertEqual(call(packet={'claim':claim,'target':{**target,'path':survivor}},status=403,prefix=DELETE),
            {'error':'STORAGE_DELETE_CLAIM_DENIED'})
        packet={'claim':claim,'target':target}
        proof=call(packet=packet,prefix=DELETE)
        self.assertEqual(proof,{'ok':True,'operationId':claim['taskId'],'targetAbsent':True,'recoveryId':proof['recoveryId']})
        self.assertEqual(call(packet=packet,prefix=DELETE),proof)
        self.assertFalse(f.parent.exists())
        self.assertEqual(sorted(p.name for p in bgdir.iterdir()),before_backgrounds)
        self.assertTrue(f.parent.with_name(survivor).joinpath('1.json').exists())
        quarantined=f.parent.parent/'.storage-quarantine'/proof['recoveryId']/'target'
        self.assertEqual((quarantined/'1.json').read_bytes(),original)
        self.assertEqual(json.loads((quarantined/'2.collisions.json').read_text())['colliders'],changed['colliders'])
        with self.assertRaises(Conflict):map_revision(store,state,True)
        with self.assertRaises(Conflict):map_revision(store,{**state,'graph':changed,'revision':3},True)
