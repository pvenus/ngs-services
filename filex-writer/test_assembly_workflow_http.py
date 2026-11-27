"""Private real Planner DAG, PDS workflow and scoped Filex assembly proof."""
import json,sqlite3,subprocess
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from media_store import CinematicProjectStore
from map_store import map_revision
from project_data_service.filex_cas_transport import FilexCASTransport
from project_data_service.http_server import Handler
from project_data_service.workflow_delete_claim import PrivatePlannerClaim
from project_data_service.workflow_assembly import WorkflowAssembly,PATH,CAP
from test_cinematic_sequence_http import REPO
from test_prop_package_http import make
from test_scope_registry import UnifiedFixture

class AssemblyFixture(UnifiedFixture):
    def test_generate_update_and_bind_actual_published_static_bytes(self):
        live_props={}
        for i,(role,name) in enumerate((('table','ritual-seal-altar-v1'),('bowl','ritual-seal-ash-catcher-v2'),
                ('well','ritual-seal-well-v3'),('lid','ritual-seal-well-cover-v5')),1):
            e=make(str(i),prop_id=name)
            self.call('/v1/scopes/props/workflow/props','concept',{k:e[k] for k in ('candidate','manifest','media')})
            live_props[role]=e
        self.call('/v1/scopes/cinematics/workflow/props/assembly-bindings','media',{},status=404)
        map_id='request.official.'+'9'*64
        c=CinematicProjectStore(self.data/'cinematics')
        graph={'title':'맵','regions':[{'id':f'zone-{n}'} for n in range(1,6)],'startRegionId':'zone-1',
               'connections':[{'from':f'zone-{n}','to':f'zone-{n+1}'} for n in range(1,5)]}
        map_revision(c,{'schemaVersion':'ngs.map-metadata.v1','projectId':'binari','mapId':map_id,'officialRequestId':map_id,
                        'updatedAt':1,'revision':1,'graph':graph},True)
        db=self.root/'planner.sqlite'
        script="""import {PlannerStore} from './workflow-planner/planner.mjs';
import {canonical,sha} from './workflow-planner/envelope.mjs';
const [path,officialRequestId,body,requestType,stamp]=process.argv.slice(1),payload=JSON.parse(body),s=new PlannerStore(path),payloadSha256=sha(canonical(payload));
s.stage({officialRequestId,requestType,projectId:'binari',schemaVersion:payload.schemaVersion,payload,payloadSha256,registeredAt:1,status:'registered'});
s.acknowledge(officialRequestId);
s.db.prepare("UPDATE workflow_tasks SET status='running',updated_at=? WHERE official_request_id=?").run(+stamp,officialRequestId);
console.log(JSON.stringify({officialRequestId,projectId:'binari',taskId:s.db.prepare('SELECT task_id FROM workflow_tasks WHERE official_request_id=?').get(officialRequestId).task_id,payloadSha256,claimStamp:+stamp}));
s.close();"""
        def stage(n,body,kind,stamp):
            return json.loads(subprocess.check_output(['node','--input-type=module','-e',script,str(db),
                'request.official.'+n*64,json.dumps(body),kind,str(stamp)],cwd=REPO))
        g={'schemaVersion':'ngs.assembly-generate.v1','projectId':'binari','title':'굿터','mapId':map_id,
           'puzzleType':'binari.ritual-well-water-demo','intent':'제상과 우물을 조립'}
        gen=stage('a',g,'assembly.generate',11)
        db.chmod(0o600);st=db.stat()
        def factory(scope,auth):
            return FilexCASTransport({'schemaVersion':'pds-filex-cas.v1','projectId':'binari','enabled':True,
                'endpoint':self.origin,'tokenFile':str(self.secret/auth/'writer_token'),'writerScope':scope})
        app=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        app.workflow_assembly=WorkflowAssembly(PrivatePlannerClaim(str(db),st.st_dev,st.st_ino),
            writer_factory=lambda:factory('cinematics','media'),prop_factory=lambda:factory('props','concept'))
        thread=Thread(target=app.serve_forever,daemon=True);thread.start()
        def stop():app.shutdown();app.server_close();thread.join()
        self.addCleanup(stop)
        def call(tail='',packet=None,expect=200):
            req=Request(f'http://127.0.0.1:{app.server_port}'+PATH+tail,
                data=None if packet is None else json.dumps(packet).encode(),
                headers={} if packet is None else {'Content-Type':'application/json'})
            try:
                with urlopen(req,timeout=5) as r:self.assertEqual(r.status,expect);return json.load(r)
            except HTTPError as err:
                with err:self.assertEqual(err.code,expect);return json.load(err)
        self.assertEqual(call(),{'enabled':True,'schemaVersion':CAP})
        self.assertEqual(call('/list',{}),{'assemblies':[]})
        call('/read',{'claim':{**gen,'claimStamp':6}},403)
        generated=call('/read',{'claim':gen})
        self.assertEqual(generated['context'],{k:graph[k] for k in ('regions','connections','startRegionId')})
        draft=generated['state'];puzzle= draft['puzzles'][0];aid=draft['assemblyId']
        model={**puzzle,'layout':{**puzzle['layout'],'altar':{**puzzle['layout']['altar'],'x':46}},
               'attachments':{**puzzle['attachments'],'lid':{'x':6,'y':-11}}}
        st1={**draft,'puzzles':[model]}
        self.assertEqual(aid,gen['officialRequestId'])
        self.assertEqual([x['regionId'] for x in puzzle['layout'].values()],['zone-2','zone-2'])
        self.assertEqual(st1['map'],{'mapId':map_id,'officialRequestId':map_id,'revision':1})
        self.assertEqual({k:v['propId'] for k,v in puzzle['bindings'].items()},{k:e['manifest']['propId'] for k,e in live_props.items()})
        call('',{'claim':gen,'state':{**st1,'puzzles':[]}},409)
        changed_pin={**model,'bindings':{**model['bindings'],'bowl':{**model['bindings']['bowl'],'sha256':'a'*64}}}
        self.assertEqual(call('',{'claim':gen,'state':{**draft,'puzzles':[changed_pin]}},409),{'error':'ASSEMBLY_INPUT_CHANGED'})
        scale={'table':110,'bowl':85,'well':165,'lid':80}
        self.assertEqual(call('',{'claim':gen,'state':{**draft,'puzzles':[{**model,'scale':scale}]}},409),
            {'error':'ASSEMBLY_INPUT_CHANGED'})
        failed_route={**model,'layout':{**model['layout'],'well':{**model['layout']['well'],'regionId':'zone-4'}}}
        self.assertEqual(call('',{'claim':gen,'state':{**draft,'puzzles':[failed_route]}},409),{'error':'ASSEMBLY_ROUTE_NOT_PLAYABLE'})
        self.assertEqual(call('/list',{}),{'assemblies':[]})
        first=call('',{'claim':gen,'state':st1})
        self.assertEqual(first['resultRef'],f'binari/cinematics/assemblies/{aid}/1.json')
        root=self.data/'cinematics'/'assemblies'/aid
        initial=(root/'1.json').read_bytes()
        self.assertEqual(json.loads((root/'revision.json').read_text()),{'revision':1,'officialRequestId':aid})
        with sqlite3.connect(db) as conn:
            conn.execute('UPDATE workflow_tasks SET updated_at=12 WHERE task_id=?',(gen['taskId'],))
        self.assertEqual(call('/read',{'claim':{**gen,'claimStamp':12}}),{**generated,'state':st1})
        self.assertEqual(call('',{'claim':{**gen,'claimStamp':12},'state':st1}),first)
        self.assertEqual(call('',{'claim':{**gen,'claimStamp':12},'state':draft},409),{'error':'ASSEMBLY_INPUT_CHANGED'})
        def complete(claim,receipt):
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE workflow_tasks SET status='completed',result_ref=? WHERE task_id=?",(receipt['resultRef'],claim['taskId']))
        complete(gen,first)
        self.assertEqual(call('/result',{'officialRequestId':aid}),first)

        # The v2 request retains ordered IDs and receives one shared map context.
        multi_gen=stage('8',{**{k:v for k,v in g.items() if k!='puzzleType'},'schemaVersion':'ngs.assembly-generate.v2',
            'puzzles':[{'id':'first-water','type':g['puzzleType'],'intent':'왼쪽 제상'},
                       {'id':'second-water','type':g['puzzleType'],'intent':''}]},'assembly.generate',12)
        md=call('/read',{'claim':multi_gen})
        self.assertEqual(md['context'],generated['context'])
        batch=md['state'];self.assertEqual([x['id'] for x in batch['puzzles']],['first-water','second-water'])
        self.assertEqual(call('',{'claim':multi_gen,'state':{**batch,'puzzles':list(reversed(batch['puzzles']))}},409),
            {'error':'ASSEMBLY_INPUT_CHANGED'})
        batch={**batch,'puzzles':[{**p,'layout':{**p['layout'],'altar':{**p['layout']['altar'],'x':41+i*6}}}
            for i,p in enumerate(batch['puzzles'])]}
        second_assembly=call('',{'claim':multi_gen,'state':batch})
        complete(multi_gen,second_assembly)
        self.assertEqual(call('/result',{'officialRequestId':batch['assemblyId']}),second_assembly)

        # Published prop head may advance, but the exact older pin remains usable.
        bowl=live_props['bowl']['manifest']
        v2=make('5','regenerate',bowl,70,prop_id=bowl['propId'])
        self.call('/v1/scopes/props/workflow/props','concept',{k:v2[k] for k in ('candidate','manifest','media')})
        u={'schemaVersion':'ngs.assembly-update.v1','projectId':'binari','assemblyId':aid,'expectedRevision':1}
        bad={**puzzle,'layout':{**puzzle['layout'],'well':{**puzzle['layout']['well'],'regionId':'zone-4'}}}
        bad_claim=stage('b',{**u,'puzzles':[bad]},'assembly.update',13)
        self.assertEqual(call('/read',{'claim':bad_claim},409),{'error':'ASSEMBLY_ROUTE_NOT_PLAYABLE'})
        # The same cross-room edit becomes valid as soon as the map publishes its return path.
        map_revision(c,{'schemaVersion':'ngs.map-metadata.v1','projectId':'binari','mapId':map_id,
                        'officialRequestId':'request.official.'+'7'*64,'updatedAt':13,'revision':2,
                        'graph':{**graph,'connections':graph['connections']+[
                            {'from':'zone-4','to':'zone-3'},{'from':'zone-3','to':'zone-2'}]}},True)
        good={**bad,'scale':scale,'layout':{**bad['layout'],'well':{**bad['layout']['well'],'x':71}}}
        edit=stage('c',{**u,'puzzles':[good]},'assembly.update',14)
        prepared=call('/read',{'claim':edit})['state'];second=call('',{'claim':edit,'state':prepared})
        complete(edit,second)
        saved=json.loads((root/'2.json').read_text())
        self.assertEqual(saved['puzzles'][0]['scale'],scale)
        self.assertEqual(saved['puzzles'][0]['layout']['well']['regionId'],'zone-4')
        self.assertEqual(saved['map'],st1['map'])  # immutable provenance; no rebind or rewrite
        self.assertNotIn('scale',json.loads((root/'1.json').read_text())['puzzles'][0])
        self.assertEqual(call('/result',{'officialRequestId':edit['officialRequestId']}),second)
        self.assertEqual((root/'1.json').read_bytes(),initial)
        stale=stage('d',{**u,'puzzles':[]},'assembly.update',15)
        self.assertEqual(call('/read',{'claim':stale},409),{'error':'ASSEMBLY_REVISION_CONFLICT'})
        # Backing bytes removed despite an intact manifest must not yield a new saved revision.
        media=bowl['media']['pixel']['assetId']
        (self.data/'props'/bowl['propId']/'assets'/media).unlink()
        missing=stage('e',{**u,'expectedRevision':2,'puzzles':[good]},'assembly.update',16)
        self.assertEqual(call('/read',{'claim':missing},409),{'error':'ASSEMBLY_PROP_UNAVAILABLE'})
        clear=stage('f',{**u,'expectedRevision':2,'puzzles':[]},'assembly.update',17)
        empty=call('/read',{'claim':clear})['state'];self.assertEqual(empty['puzzles'],[])
        call('',{'claim':clear,'state':empty})
        self.assertEqual(call('/list',{}),{'assemblies':[empty,batch]})
