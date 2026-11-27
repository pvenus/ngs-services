"""Private real Planner DAG, PDS workflow and scoped Filex assembly proof."""
import base64,json,sqlite3,subprocess
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from media_store import CinematicProjectStore
from map_store import map_revision
from project_data_service.filex_cas_transport import FilexCASTransport
from project_data_service.http_server import Handler
from project_data_service.workflow_delete_claim import PrivatePlannerClaim
from project_data_service.workflow_file_delete import ASSEMBLY_ROOT,storage_provider
from project_data_service.workflow_file_update import WorkflowFileUpdate,PATH as UPDATE
from project_data_service.single_target_delete import ASSEMBLY_CAPABILITY,PATH as DELETE,SCHEMA,SingleTargetDelete
from project_data_service.workflow_assembly import WorkflowAssembly,PATH,CAP
from test_cinematic_sequence_http import REPO
from test_prop_package_http import make,artifact_store
from project_data_service.filex_read import digest
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
               'connections':[{'id':f'edge-{n}','from':f'zone-{n}','to':f'zone-{n+1}'} for n in range(1,5)]}
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
                'request.official.'+(n*64)[:64],json.dumps(body),kind,str(stamp)],cwd=REPO))
        g={'schemaVersion':'ngs.assembly-generate.v1','projectId':'binari','title':'굿터','mapId':map_id,
           'puzzleType':'binari.ritual-well-water-demo','intent':'제상과 우물을 조립'}
        gen=stage('a',g,'assembly.generate',11)
        db.chmod(0o600);st=db.stat()
        def factory(scope,auth):
            return FilexCASTransport({'schemaVersion':'pds-filex-cas.v1','projectId':'binari','enabled':True,
                'endpoint':self.origin,'tokenFile':str(self.secret/auth/'writer_token'),'writerScope':scope})
        app=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        planner=PrivatePlannerClaim(str(db),st.st_dev,st.st_ino)
        def cine():
            return factory('cinematics','media')
        def delete_ready():
            with cine() as conn:return conn.capabilities().get('workflowPinnedAssemblyFolderDeleteV1') is True
        app.workflow_assembly=WorkflowAssembly(planner,writer_factory=cine,prop_factory=lambda:factory('props','concept'))
        app.workflow_file_update=WorkflowFileUpdate(planner,writer_factory=cine,prop_factory=lambda:factory('props','concept'))
        app.single_target_delete=SingleTargetDelete({ASSEMBLY_ROOT:storage_provider(planner,cine,delete_ready,root=ASSEMBLY_ROOT)},
            dedicated=[ASSEMBLY_ROOT])
        thread=Thread(target=app.serve_forever,daemon=True);thread.start()
        def stop():app.shutdown();app.server_close();thread.join()
        self.addCleanup(stop)
        def call(tail='',packet=None,expect=200,base=PATH):
            req=Request(f'http://127.0.0.1:{app.server_port}'+base+tail,
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
        # Official full metadata update saves portal rules; no new public request type.
        batch_id=batch['assemblyId'];batch_ref=batch['map']
        cond={'portalId':'edge-1','propRole':'bowl','requiredState':'carried-filled'}
        rows=[{**batch['puzzles'][0],'portalConditions':[cond]},batch['puzzles'][1]]
        def file_update(key,pin,rows,title=None):
            payload={'schemaVersion':'ngs.file-update.v1','projectId':'binari',
                     'target':{'root':ASSEMBLY_ROOT,'path':batch_id,'kind':'metadata'},'expected':pin,
                     'content':{'title':title or batch['title'],'mapId':map_id,'puzzles':rows}}
            return stage(key,payload,'file.update',18)
        pin0={'revision':1,'officialRequestId':batch_id}
        wrongTitle=file_update('0',pin0,rows,title='must not rename')
        self.assertEqual(call(packet={'claim':wrongTitle},expect=409,base=UPDATE),{'error':'FILE_UPDATE_ASSEMBLY_IDENTITY_IMMUTABLE'})
        change=file_update('1',pin0,rows)
        receipt=call(packet={'claim':change},base=UPDATE)
        self.assertEqual(call(packet={'claim':change},base=UPDATE),receipt)
        complete(change,receipt)
        self.assertEqual(call('/result',{'officialRequestId':change['officialRequestId']},base=UPDATE),receipt)
        path=self.data/'cinematics'/'assemblies'/batch_id
        batch=json.loads((path/'2.json').read_text())
        self.assertEqual(batch['puzzles'],rows)
        self.assertEqual(batch['map'],batch_ref)

        latest={**graph,'connections':[{**e,'id':'replacement' if e['id']=='edge-1' else e['id']} for e in graph['connections']]+[
            {'id':'return-4','from':'zone-4','to':'zone-3'},{'id':'return-3','from':'zone-3','to':'zone-2'}]}
        map_revision(c,{'schemaVersion':'ngs.map-metadata.v1','projectId':'binari','mapId':map_id,
                        'officialRequestId':'request.official.'+'6'*64,'updatedAt':15,'revision':3,'graph':latest},True)
        pin1={'revision':2,'officialRequestId':change['officialRequestId']}
        bad=[{**rows[0],'portalConditions':[{**cond,'requiredState':'altar-empty'}]},rows[1]]
        refused=file_update('2',pin1,bad)
        self.assertEqual(call(packet={'claim':refused},expect=409,base=UPDATE),{'error':'FILE_UPDATE_ASSEMBLY_PORTAL_MISSING'})
        unchanged=[{**rows[0],'scale':scale},rows[1]]
        unrelated=file_update('3',pin1,unchanged)
        call(packet={'claim':unrelated},base=UPDATE)
        batch=json.loads((path/'3.json').read_text())
        self.assertEqual(batch['puzzles'],unchanged)
        self.assertEqual(batch['map'],batch_ref)
        stale_update=file_update('4',pin1,rows)
        self.assertEqual(call(packet={'claim':stale_update},expect=409,base=UPDATE),{'error':'FILE_UPDATE_REVISION_CONFLICT'})
        # A direct writer request is also fenced against an absent changed portal.
        invalid={**batch,'revision':4,'officialRequestId':'request.official.'+'0'*64,'puzzles':bad}
        self.call('/v1/scopes/cinematics/workflow/assemblies/metadata-update','media',
                  {'expected':{'revision':3,'officialRequestId':batch['officialRequestId']},'state':invalid},status=409)

        # A spirit puzzle can be added by metadata edit, not generated by legacy assembly.
        legacy_pin={'propId':live_props['lid']['manifest']['propId'],'revision':1,'sha256':live_props['lid']['candidate']['manifestSha256']}
        spirit={'id':'ghost-one','type':'binari.ritual-spirit-manifestation','layout':{'spirit':{'regionId':'zone-2','x':43,'y':67}},
                'attachments':{},'bindings':{'spirit':legacy_pin},'scale':{'spirit':150}}
        expected={'revision':3,'officialRequestId':batch['officialRequestId']}
        require_both=file_update('9',expected,[*unchanged,spirit])
        self.assertEqual(call(packet={'claim':require_both},expect=409,base=UPDATE),
            {'error':'FILE_UPDATE_ASSEMBLY_SPIRIT_PROP_UNAVAILABLE'})
        layered=make('e',prop_id='ghost-fixture');manifest=layered['manifest'];candidate=layered['candidate']
        for i,lane in enumerate(('pixel','spirit')):
            m,raw=artifact_store(6,6,222+i)
            manifest['media'][lane]=m
            layered['media'][m['assetId']]=base64.b64encode(raw).decode()
            if lane=='pixel':candidate['members'][-1]['ref']='prop.pixel-rgba.'+m['sha256']
            else:
                key='prop_spirit'
                candidate['members'].append({'key':key,'taskId':'workflow.task.'+digest([manifest['officialRequestId'],key]),
                    'ref':'prop.spirit-rgba.'+m['sha256']})
        candidate['scope']['presentationProfile']='spirit-v1'
        candidate['manifestSha256']=digest(manifest);candidate['resultRef']='prop.package.'+digest(manifest)
        asset_ids={m['assetId'] for m in manifest['media'].values()}
        layered['media']={k:v for k,v in layered['media'].items() if k in asset_ids}
        self.call('/v1/scopes/props/workflow/props','concept',{k:layered[k] for k in ('candidate','manifest','media')})
        pin={'propId':'ghost-fixture','revision':1,'sha256':digest(manifest)}
        spirit={**spirit,'bindings':{'spirit':pin}}
        v3={**{k:v for k,v in g.items() if k!='puzzleType'},'schemaVersion':'ngs.assembly-generate.v3',
            'puzzles':[{'id':'test-water','type':g['puzzleType'],'intent':'우물'},
                       {'id':'test-spirit','type':spirit['type'],'intent':'그림자','prop':pin}]}
        missing_lane=stage('d3',{**v3,'puzzles':[v3['puzzles'][0],{**v3['puzzles'][1],'prop':legacy_pin}]},'assembly.generate',20)
        self.assertEqual(call('/read',{'claim':missing_lane},409),{'error':'ASSEMBLY_PROP_UNAVAILABLE'})
        mixed=stage('c3',v3,'assembly.generate',20)
        prepared=call('/read',{'claim':mixed})['state']
        self.assertEqual(prepared['puzzles'][1]['bindings']['spirit'],pin)
        self.assertNotEqual(prepared['puzzles'][1]['layout'],spirit['layout'])
        authored={**prepared,'puzzles':[prepared['puzzles'][0],{**spirit,'id':'test-spirit'}]}
        wrong_pin={**authored,'puzzles':[authored['puzzles'][0],{**authored['puzzles'][1],'bindings':{'spirit':legacy_pin}}]}
        self.assertEqual(call('',{'claim':mixed,'state':wrong_pin},409),{'error':'ASSEMBLY_INPUT_CHANGED'})
        unreachable={**authored,'puzzles':[authored['puzzles'][0],{**authored['puzzles'][1],
            'layout':{'spirit':{'regionId':'not-on-map','x':44,'y':67}}}]}
        self.assertEqual(call('',{'claim':mixed,'state':unreachable},409),{'error':'ASSEMBLY_ROUTE_NOT_PLAYABLE'})
        mixed_receipt=call('',{'claim':mixed,'state':authored})
        self.assertEqual(call('',{'claim':mixed,'state':authored}),mixed_receipt)
        complete(mixed,mixed_receipt)
        self.assertEqual(call('/result',{'officialRequestId':authored['assemblyId']}),mixed_receipt)
        # A valid region need not be symmetric, but must be reachable from start.
        bad_station={**spirit,'layout':{'spirit':{**spirit['layout']['spirit'],'regionId':'not-on-map'}}}
        blocked=file_update('a5',expected,[bad_station])
        self.assertEqual(call(packet={'claim':blocked},expect=409,base=UPDATE),
            {'error':'FILE_UPDATE_ASSEMBLY_ROUTE_NOT_PLAYABLE'})
        add_spirit=file_update('b5',expected,[*unchanged,spirit])
        result=call(packet={'claim':add_spirit},base=UPDATE)
        complete(add_spirit,result)
        batch=json.loads((path/'4.json').read_text())
        self.assertEqual(batch['puzzles'][-1],spirit)
        self.assertEqual(call('/result',{'officialRequestId':add_spirit['officialRequestId']},base=UPDATE),result)

        # Backing bytes removed despite an intact manifest must not yield a new saved revision.
        media=bowl['media']['pixel']['assetId']
        (self.data/'props'/bowl['propId']/'assets'/media).unlink()
        missing=stage('e',{**u,'expectedRevision':2,'puzzles':[good]},'assembly.update',16)
        self.assertEqual(call('/read',{'claim':missing},409),{'error':'ASSEMBLY_PROP_UNAVAILABLE'})
        clear=stage('f',{**u,'expectedRevision':2,'puzzles':[]},'assembly.update',17)
        empty=call('/read',{'claim':clear})['state'];self.assertEqual(empty['puzzles'],[])
        call('',{'claim':clear,'state':empty})
        self.assertEqual(call('/list',{}),{'assemblies':[batch,authored,empty]})

        self.assertEqual(call(base=ASSEMBLY_CAPABILITY),{'enabled':True,'roots':[ASSEMBLY_ROOT],'schemaVersion':SCHEMA})
        self.assertEqual(call(base=DELETE)['roots'],[])
        target={'root':ASSEMBLY_ROOT,'path':aid,'kind':'directory'}
        def deletion(key,revision,publisher):
            payload={'schemaVersion':'ngs.file-delete.v1','projectId':'binari','target':target,
                     'expected':{'revision':revision,'officialRequestId':publisher}}
            claim=stage(key,payload,'file.delete',19)
            return {'claim':claim,'target':target}
        self.assertEqual(call(packet=deletion('7',1,aid),expect=409,base=DELETE),{'error':'STORAGE_DELETE_STALE_TARGET'})
        self.assertEqual(call(packet=deletion('6',3,aid),expect=409,base=DELETE),{'error':'STORAGE_DELETE_STALE_TARGET'})
        self.assertTrue(root.is_dir())
        command=deletion('5',3,clear['officialRequestId'])
        wrong={**command,'target':{**target,'path':batch['assemblyId']}}
        self.assertEqual(call(packet=wrong,expect=403,base=DELETE),{'error':'STORAGE_DELETE_CLAIM_DENIED'})
        receipt=call(packet=command,base=DELETE)
        self.assertTrue(receipt['targetAbsent'])
        self.assertFalse(root.exists())
        self.assertEqual(call(packet=command,base=DELETE),receipt)
        self.assertEqual(call('/list',{}),{'assemblies':[batch,authored]})
        self.assertEqual(call('/read',{'claim':clear},409),{'error':'ASSEMBLY_REVISION_CONFLICT'})
        self.call('/v1/scopes/cinematics/workflow/assemblies','media',st1,status=409)
        self.assertTrue((self.data/'cinematics'/'maps'/map_id/'1.json').is_file())
        self.assertTrue((self.data/'props'/bowl['propId']/'metadata.json').is_file())
        recovery=root.parent/'.storage-quarantine'/receipt['recoveryId']/'target'
        self.assertEqual((recovery/'1.json').read_bytes(),initial)

        # A v4 official request has a fresh identity and cannot publish its scaffold.
        from assembly_contract import WATER_STATES,WATER_RULES
        v4={**v3,'schemaVersion':'ngs.assembly-generate.v4','puzzles':[v3['puzzles'][0]]}
        v4_claim=stage('e4',v4,'assembly.generate',30)
        draft=call('/read',{'claim':v4_claim})['state']
        self.assertEqual(call('',{'claim':v4_claim,'state':draft},409),{'error':'ASSEMBLY_INPUT_CHANGED'})
        rules=[{'module':module,'action':{'kind':kind,'target':target,'item':item},
                'from':{'role':role,'state':source},'to':{'role':role,'state':destination},
                'requires':[{'role':k,'state':v} for k,v in conditions]}
               for module,kind,target,item,role,source,destination,conditions in WATER_RULES]
        definition={'schemaVersion':'ngs.puzzle-definition.v1',
                    'entities':[{'role':role,'initial':next(iter(states)),
                                 'states':[{'id':i,'held':h,'payload':p} for i,(h,p) in states.items()]}
                                for role,states in WATER_STATES.items()],
                    'rules':rules,'goal':{'role':'bowl','state':'altar-filled'},
                    'solution':[r['action'] for r in rules]}
        row={**draft['puzzles'][0],'definition':definition}
        made={**draft,'puzzles':[row]}
        self.call('/v1/scopes/cinematics/workflow/assemblies','media',made,status=400) # no v4 envelope
        cheated={**made,'puzzles':[{**row,'definition':{**definition,'solution':list(reversed(definition['solution']))}}]}
        self.assertEqual(call('',{'claim':v4_claim,'state':cheated},409),{'error':'ASSEMBLY_INPUT_CHANGED'})
        v4_receipt=call('',{'claim':v4_claim,'state':made})
        complete(v4_claim,v4_receipt)
        self.assertEqual(call('/result',{'officialRequestId':made['assemblyId']}),v4_receipt)
        def edit_v4(i,rows):
            return stage(i,{'schemaVersion':'ngs.file-update.v1','projectId':'binari',
                'target':{'root':ASSEMBLY_ROOT,'path':made['assemblyId'],'kind':'metadata'},
                'expected':{'revision':1,'officialRequestId':made['officialRequestId']},
                'content':{'title':made['title'],'mapId':map_id,'puzzles':rows}},'file.update',31)
        missing=edit_v4('a4',draft['puzzles'])
        self.assertEqual(call(packet={'claim':missing},expect=409,base=UPDATE),
                         {'error':'FILE_UPDATE_ASSEMBLY_DEFINITION_IMMUTABLE'})
        moved={**row,'attachments':{**row['attachments'],'lid':{'x':3,'y':-9}}}
        ok_edit=edit_v4('b4',[moved])
        call(packet={'claim':ok_edit},base=UPDATE)
        self.assertEqual(json.loads((self.data/'cinematics'/'assemblies'/made['assemblyId']/'2.json').read_text())['puzzles'],[moved])
