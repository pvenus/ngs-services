"""Actual Planner-created SQLite -> read-only PDS stages -> authenticated Filex."""
import base64,json,os,sqlite3,struct,subprocess,time,zlib
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from unittest import main,TestCase
from unittest.mock import patch
from test_prop_package_http import PropFixture,PROP,artifact_store,make

from prop_package_contract import wire,digest,sha
from project_data_service.prop_design_context import PropDesignContext
from project_data_service.prop_stage_claim import (PropClaimAuthority,task_id,build_graph,CANDLE_CHROMA_PROP,CANDLE_CHROMA_PROFILE)
from project_data_service.prop_stage_store import PropStageStore
from project_data_service.workflow_delete_claim import PrivatePlannerClaim
from project_data_service.workflow_prop_package import PATH,WRITER,WorkflowPropPackage,bound_prop_writer
from project_data_service.workflow_prop_stage import DESIGN_ROUTE,STAGE,RealPropAuthority,expected_interaction

REPO=Path(__file__).resolve().parents[1]
FIELDS=[{'id':k,'text':json.dumps({'schemaVersion':v},ensure_ascii=False,sort_keys=True,separators=(',',':'))}
        for k,v in [('visualStyle','ngs.character-style-contract.v1'),('visualProhibitions','ngs.character-style-constraints.v1')]]
for row in FIELDS:row['sha256']=sha(row['text'].encode())
MARKDOWN='# 프랍 디자인 설계\n\n## 형태와 식별\n단정한 형태\n## 재료와 색\n나무\n## 상태 사이의 일관성\n모양 유지\n## 납품과 검수\n검수\n'
SOURCES=[{'role':'master-design','categoryId':'expression','sha256':digest(FIELDS),'fields':FIELDS},
 {'role':'prop-design','categoryId':'category.add.26710502f5713d99018b77a29384d5da','sha256':sha(MARKDOWN.encode()),'markdown':MARKDOWN}]
DESIGN_HASH=digest([[r[k] for k in ('role','categoryId','sha256')] for r in SOURCES])
CONTEXT={'projectId':'binari','schemaVersion':'ngs.prop-image-design.v1','contextSha256':DESIGN_HASH,'sources':SOURCES}

class Context(PropDesignContext):
    # Context call boundary only (canonical two-source integration has its own
    # real Filex fixture). No claim, storage or publisher is replaced here.
    def __init__(self):pass
    def read(self,project):
        assert project=='binari';return CONTEXT

class StageFixture(PropFixture,TestCase):
    def setUp(self):
        super().setUp()
        parent=Path(self.temp.name).resolve()
        db=parent/'planner.sqlite'
        self.register_generate(db)
        stat=db.stat()
        stage=parent/'prop-stage';stage.mkdir(mode=0o700)
        actual=RealPropAuthority(PropClaimAuthority(PrivatePlannerClaim(str(db),stat.st_dev,stat.st_ino)),
                                 PropStageStore(stage),Context(),self.filex_writer)
        self.pds=self.launch(self.handler,workflow_prop_package=WorkflowPropPackage(actual,self.filex_writer),
                             workflow_prop_stage=actual)
        self.actual=actual

    def register_generate(self,db,profile=False,scales=None):
        blob={
          'schemaVersion':'ngs.prop-generate.v1','projectId':'binari','propId':PROP,
          'intent':'테스트 등롱','designContextSha256':DESIGN_HASH}
        if profile:blob['presentationProfile']=profile
        if scales is not None:blob['scalePercents']=scales
        self.oid='request.official.'+('c' if profile else 'b')*64
        subprocess.run(['node','--input-type=module','-e',"""import {PlannerStore} from './workflow-planner/planner.mjs';
import {canonical,sha} from './workflow-planner/envelope.mjs';
const [db,id,raw]=process.argv.slice(1),payload=JSON.parse(raw);
const s=new PlannerStore(db,{propGenerateEnabled:true,readPropPublication:async()=>null});
s.stage({officialRequestId:id,requestType:'prop.generate',projectId:'binari',schemaVersion:payload.schemaVersion,
        payload,payloadSha256:sha(canonical(payload)),status:'registered',registeredAt:1});
s.acknowledge(id);
s.close();
""",str(db),self.oid,json.dumps(blob,ensure_ascii=False)],cwd=REPO,check=True,capture_output=True)
        os.chmod(db,0o600)
        self.db=db;self.payload=blob;self.payload_sha=digest(blob)

    def mark(self,key,stamp,ref=None):
        t=task_id(self.oid,key)
        with sqlite3.connect(self.db) as conn:
            conn.execute('UPDATE workflow_tasks SET status=?,result_ref=?,updated_at=? WHERE task_id=?',
                         ('completed' if ref else 'running',ref,stamp,t))
        return {'officialRequestId':self.oid,'taskId':t,'projectId':'binari','payloadSha256':self.payload_sha,'claimStamp':stamp}
    def stage(self,route,path,body,expected=200,raw=False):
        q=Request(self.pds+route+path,data=wire(body),headers={'Content-Type':'application/json'},method='POST')
        try:
            with urlopen(q,timeout=6) as r:
                self.assertEqual(r.status,expected);return r.read() if raw else json.load(r)
        except HTTPError as e:
            with e:self.assertEqual(e.code,expected);return json.load(e)
    def test_actual_document_worker_claim_transport_and_completion(self):
        package=os.getenv('PROP_DOCUMENT_MODULE_DIR')
        if not package:self.skipTest('optional exact staged document-worker source')
        cmd="""import {pathToFileURL} from 'node:url';import {join} from 'node:path';
const [root,db,oid,tid,port]=process.argv.slice(1);
const imp=name=>import(pathToFileURL(join(root,name)).href);
const {localPorts,requestLocalJson}=await imp('local-ports.mjs');
const {runPropDesignFromPath}=await imp('prop-design-worker.mjs');
const pds=localPorts({transport:args=>requestLocalJson({...args,port:Number(port)})});
console.log(JSON.stringify(await runPropDesignFromPath({dbPath:db,officialRequestId:oid,taskId:tid,
 operation:'prop.generate.design',pds,render:async input=>{
 if(input.sources.length!==2||input.intent!=='테스트 등롱')throw Error('fixture input');
 return {title:'등롱',appearancePrompt:'종이 등롱',motionPrompt:null,interaction:{pixelOpen:null,detailTrigger:null}};
 }})));"""
        task=task_id(self.oid,'prop_design')
        execution=subprocess.run(['node','--input-type=module','-e',cmd,package,str(self.db),self.oid,task,
            self.pds.rsplit(':',1)[1]],cwd=REPO,check=True,capture_output=True)
        r=json.loads(execution.stdout)
        self.assertEqual(r['state'],'completed',r)
        self.assertTrue(r['resultRef'].startswith('prop.design-spec.'))
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT status,result_ref FROM workflow_tasks WHERE task_id=?',(task,)).fetchone(),
                ('completed',r['resultRef']))
        self.assertEqual(self.actual.staging.get(self.oid,task,'prop_design',r['resultRef'])['artifact']['title'],'등롱')
    def test_python_claim_dag_matches_actual_js_planner_for_every_operation(self):
        source={'revision':1,'sha256':'c'*64};all_rows=[]
        for kind,extra,schema in (
            ('prop.generate',{'motion':'subtle'},'ngs.prop-generate.v1'),
            ('prop.generate',{'presentationProfile':'spirit-v1'},'ngs.prop-generate.v1'),
            ('prop.generate',{'presentationProfile':'spirit-v2'},'ngs.prop-generate.v1'),
            ('prop.generate',{'presentationProfile':'spirit-v2','scalePercents':[100,10,5,20,30,40,50,60,70,80,90],'motion':'subtle'},'ngs.prop-generate.v1'),
            ('prop.scale.generate',{'source':source,'scalePercents':[100,5]},'ngs.prop-scale-generate.v1'),
            ('prop.regenerate',{'source':source,'motion':'subtle','presentationProfile':'spirit-v2'},'ngs.prop-regenerate.v1'),
            ('prop.regenerate',{'source':source,'motion':'subtle','presentationProfile':'spirit-v1'},'ngs.prop-regenerate.v1'),
            ('prop.regenerate',{'source':source,'motion':'subtle'},'ngs.prop-regenerate.v1'),
            ('prop.animation.generate',{'source':source,'motionId':'gentle-sway','prompt':'one motion'},'ngs.prop-animation-generate.v1')):
            p={**self.payload,**extra,'schemaVersion':schema}
            if kind in ('prop.animation.generate','prop.scale.generate'):
                p.pop('intent');p.pop('designContextSha256')
            all_rows.append((kind,p))
        cmd="""import {buildPlan} from './workflow-planner/planner.mjs';
import {canonical,sha} from './workflow-planner/envelope.mjs';
const [oid,arg]=process.argv.slice(1);
console.log(JSON.stringify(JSON.parse(arg).map(([requestType,payload])=>buildPlan({officialRequestId:oid,
 projectId:'binari',requestType,schemaVersion:payload.schemaVersion,payload,payloadSha256:sha(canonical(payload)),
 status:'registered',registeredAt:1}).tasks)));"""
        got=json.loads(subprocess.run(['node','--input-type=module','-e',cmd,self.oid,json.dumps(all_rows)],
           cwd=REPO,check=True,capture_output=True).stdout)
        for (kind,p),tasks in zip(all_rows,got):
            graph=build_graph(self.oid,kind,p)
            self.assertEqual(list(graph),[t['key'] for t in tasks])
            for t in tasks:
                key=t['key'];expected={'task_id':t['id'],'task_key':key,'task_type':t['type'],'worker_type':t['workerType'],
                    'task_input':wire(t['input']).decode(),'depends_on_task_id':t['dependsOn']}
                expected.update({f'depends_on_task_id_{i+2}':dep for i,dep in enumerate(t.get('additionalDependencies',[]))})
                self.assertEqual(graph[key],expected)
    def test_exact_claim_design_and_image_bytes_link_to_published_artifacts(self):
        # Actual Filex authenticated capability, Planner inode and stage-root
        # checks are all part of the readiness path.
        from project_data_service.workflow_prop_package import PATH
        with urlopen(self.pds+PATH,timeout=3) as response:
            self.assertEqual(json.load(response),{'enabled':True,'schemaVersion':'workflow.prop-package-write.v1','modes':['still']})
        # Actual reconstructed persisted node DAG must match all rows.
        rows=build_graph(self.oid,'prop.generate',self.payload)
        self.assertEqual(len(rows),5)
        d=self.mark('prop_design',2);wrong={**d,'claimStamp':1}
        self.stage(DESIGN_ROUTE,'/read',{'claim':wrong},403)
        self.assertEqual(self.stage(DESIGN_ROUTE,'/read',{'claim':d})['input']['intent'],self.payload['intent'])
        doc={'schemaVersion':'ngs.prop-design-spec.v1','propId':PROP,'designContextSha256':DESIGN_HASH,
             'title':'등롱','appearancePrompt':'종이와 나무로 만든 등롱','motion':'still','motionPrompt':None,
             'interaction':{'pixelOpen':{'xPermille':50,'yPermille':100,'widthPermille':500,'heightPermille':250},
                            'detailTrigger':None}}
        receipt=self.stage(DESIGN_ROUTE,'',{'claim':d,'artifact':doc})
        self.assertEqual(receipt['resultRef'],'prop.design-spec.'+digest(doc))
        self.mark('prop_design',2,receipt['resultRef'])
        img=self.mark('prop_base',3)
        self.assertEqual(self.stage(STAGE,'/read',{'claim':img})['design']['artifact'],doc)
        base,original=artifact_store(32,32,15)
        base_record={k:base[k] for k in ('sha256','bytes','width','height','mediaType')}
        lost=self.stage(STAGE,'',{'claim':img,'artifact':{'media':base_record,'base64':base64.b64encode(original).decode()}})
        # Simulate a persisted first write whose response the worker never received;
        # on retry a new claim must accept a different PNG rather than conflict.
        attempt=self.mark('prop_base',10)
        self.stage(STAGE,'',{'claim':img,'artifact':{'media':base_record,'base64':base64.b64encode(original).decode()}},403)
        base,original=artifact_store(32,32,75)
        base_record={k:base[k] for k in ('sha256','bytes','width','height','mediaType')}
        r=self.stage(STAGE,'',{'claim':attempt,'artifact':{'media':base_record,'base64':base64.b64encode(original).decode()}})
        self.assertNotEqual(lost['resultRef'],r['resultRef'])
        # Verify established base results remain readable in the old layout.
        root=self.actual.staging.root/self.oid/img['taskId']
        old=root/lost['resultRef']
        for path in old.iterdir():path.rename(root/path.name)
        old.rmdir()
        self.assertEqual(self.actual.staging.get(self.oid,img['taskId'],'prop_base',lost['resultRef'])['resultRef'],lost['resultRef'])
        self.mark('prop_base',10,r['resultRef'])
        detail=self.mark('prop_detail',4)
        self.assertEqual(self.stage(STAGE,'/asset',{'claim':detail,'purpose':'input','index':None},raw=True),original)
        media={}
        for key,lane,claim,dim,color in [('prop_detail','detail',detail,32,40),('prop_pixel','pixel',None,16,80)]:
            cl=claim or self.mark(key,5);datum,b=artifact_store(dim,dim,color)
            result=self.stage(STAGE,'',{'claim':cl,'artifact':{'media':datum,'base64':base64.b64encode(b).decode()}})
            self.mark(key,cl['claimStamp'],result['resultRef']);media[lane]=datum
        final=self.mark('prop_pack_publish',6)
        pack={'schemaVersion':'ngs.prop-package.v1','projectId':'binari','propId':PROP,'revision':1,'officialRequestId':self.oid,
              'requestSha256':self.payload_sha,'title':doc['title'],'designContextSha256':DESIGN_HASH,'parentSha256':None,
              'media':media,'animations':[],'interaction':expected_interaction(doc,media)}
        draft=self.api('/prepare',{'claim':final})
        self.assertEqual(draft,{'manifest':pack})
        published=self.api('',{'claim':final,'manifest':pack})
        self.assertEqual(published['resultRef'],'prop.package.'+digest(pack))
        self.api('/read',self.pin(pack),expected=403) # exact Filex result is not yet claimed completed by worker
        self.mark('prop_pack_publish',6,published['resultRef'])
        self.assertEqual(self.api('/read',self.pin(pack))['manifest'],pack)
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE workflow_tasks SET task_input='{}' WHERE task_key='prop_base'")
        self.api('/read',self.pin(pack),expected=403)

    def test_spirit_v1_static_assets(self):
        self.exercise_three_static_assets('spirit-v1')

    def test_spirit_v2_pixel_source_and_detail_assets(self):
        self.exercise_three_static_assets('spirit-v2')


    def test_selected_scale_repairs_corrupt_or_missing_same_hash_bytes(self):
        prior,pixel_detail=self.exercise_three_static_assets('spirit-v2',[20,5])
        self.assertEqual([r['scalePercent'] for r in prior['scaleVariants']],[5,20])
        self.assertEqual(prior['scaleSourcePixel']['sha256'],sha(pixel_detail))
        original_pin=self.pin(prior)
        src={k:v for k,v in original_pin.items() if k!='propId'}
        def register(marker,source,scales):
            oid='request.official.'+marker*64
            p={'schemaVersion':'ngs.prop-scale-generate.v1','projectId':'binari','propId':PROP,'source':source,'scalePercents':scales}
            subprocess.run(['node','--input-type=module','-e',"""import {PlannerStore} from './workflow-planner/planner.mjs';
import {canonical,sha} from './workflow-planner/envelope.mjs';
const [db,id,raw]=process.argv.slice(1),payload=JSON.parse(raw);
const s=new PlannerStore(db,{propGenerateEnabled:true,propMutationsEnabled:true,readPropPublication:async()=>null});
s.stage({officialRequestId:id,requestType:'prop.scale.generate',projectId:'binari',schemaVersion:payload.schemaVersion,
 payload,payloadSha256:sha(canonical(payload)),status:'registered',registeredAt:2});
s.acknowledge(id);s.close();
""",str(self.db),oid,json.dumps(p)],cwd=REPO,check=True,capture_output=True)
            self.oid=oid;self.payload_sha=digest(p)
            with sqlite3.connect(self.db) as db:
                self.assertEqual(db.execute('SELECT task_type,worker_type FROM workflow_tasks WHERE official_request_id=? ORDER BY task_type',(oid,)).fetchall(),
                                 [('prop.scale.derive','postprocess')]*len(scales)+[('prop.scale.generate.replace-publish','postprocess')])
        asset_root=self.props.root/PROP/'assets'
        old20=prior['scaleVariants'][1]
        damaged=asset_root/old20['pixel']['assetId']; missing=asset_root/old20['spirit']['assetId']
        pixel_original=damaged.read_bytes();spirit_original=missing.read_bytes()
        damaged.write_bytes(b'!'*len(pixel_original));missing.unlink()
        untouched={asset_root/a['assetId']:(asset_root/a['assetId']).stat().st_ino
                   for a in (*prior['media'].values(),prior['scaleSourcePixel'],prior['scaleVariants'][0]['pixel'],
                             prior['scaleVariants'][0]['spirit'])}
        register('e',src,[100,50,20])
        self.api('/prepare',{'claim':self.mark('prop_pack_publish',18)},expected=403)
        self.api('/derive-scale',{'claim':self.mark('prop_scale_100',13)},expected=403)
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE workflow_tasks SET status='pending' WHERE task_id=?",(task_id(self.oid,'prop_scale_100'),))
            db.execute("UPDATE workflow_tasks SET status='pending' WHERE task_id=?",(task_id(self.oid,'prop_pack_publish'),))
        for stamp,n in enumerate([20,50,100],14):
            self.run_scale(n,stamp)
            self.assertEqual(self.filex_writer(WRITER+'/head',{'propId':PROP})['pointer']['revision'],prior['revision'])
        self.assertEqual(damaged.read_bytes(),b'!'*len(pixel_original))
        self.assertFalse(missing.exists())
        claim=self.mark('prop_pack_publish',18)
        with patch('project_data_service.workflow_prop_stage.derive',side_effect=AssertionError('must not resize during manifest prep')):
            pack=self.api('/prepare',{'claim':claim})['manifest']
        self.assertEqual([x['scalePercent'] for x in pack['scaleVariants']],[5,20,50,100])
        self.assertEqual(pack['scaleVariants'][:2],prior['scaleVariants'])
        self.assertEqual(pack['media'],prior['media']);self.assertEqual(pack['scaleSourcePixel'],prior['scaleSourcePixel'])
        self.assertEqual(pack['scaleVariants'][-1]['pixel'],prior['scaleSourcePixel'])
        self.assertEqual(pack['scaleVariants'][-1]['spirit'],prior['media']['detail'])
        with patch('project_data_service.workflow_prop_stage.derive',side_effect=AssertionError('must publish staged bytes')):
            if self.worker_modules():
                with sqlite3.connect(self.db) as db:
                    db.execute("UPDATE workflow_tasks SET status='pending' WHERE task_id=?",(claim['taskId'],))
                published=self.run_postprocess('prop_pack_publish')
            else:
                published=self.api('',{'claim':claim,'manifest':pack})
                self.mark('prop_pack_publish',18,published['resultRef'])
        self.assertEqual(published['resultRef'],'prop.package.'+digest(pack))
        proof=self.api('/read',self.pin(pack))
        self.assertEqual(proof['prior'],prior)
        self.assertEqual(proof['receipt']['scope']['operation'],'replace-scales')
        self.assertEqual(self.api('/read',original_pin)['manifest'],prior)
        self.assertEqual(damaged.read_bytes(),pixel_original)
        self.assertEqual(missing.read_bytes(),spirit_original)
        self.assertEqual({path:path.stat().st_ino for path in untouched},untouched)
        for k,raw in [('pixel',pixel_original),('spirit',spirit_original)]:
            read=self.api('/asset',{'pin':original_pin,'assetId':old20[k]['assetId']})
            self.assertEqual(base64.b64decode(read['base64']),raw)
        register('f',src,[90])
        self.api('/derive-scale',{'claim':self.mark('prop_scale_090',10)},expected=409)

    def test_historical_single_publish_scale_append_uses_the_original_graph(self):
        fixture=make('a',prop_id=PROP)
        prior=fixture['manifest']
        self.filex_writer(WRITER,{k:fixture[k] for k in ('candidate','manifest','media')})
        source={'revision':prior['revision'],'sha256':digest(prior)}
        oid='request.official.'+'f'*64
        kind='prop.scale.generate'
        p={'schemaVersion':'ngs.prop-scale-generate.v1','projectId':'binari','propId':PROP,'source':source,'scalePercents':[5]}
        graph=build_graph(oid,kind,p,split_scales=False)
        self.oid=oid;self.payload_sha=digest(p)
        [row]=graph.values()
        with sqlite3.connect(self.db) as db:
            db.execute("""INSERT INTO workflow_runs(official_request_id,project_id,request_type,schema_version,payload,payload_sha256,
                        registered_at,state,updated_at,reason) VALUES(?,?,?,?,?,?,1,'planned',1,NULL)""",
                       (oid,'binari',kind,p['schemaVersion'],wire(p).decode(),self.payload_sha))
            db.execute("""INSERT INTO workflow_tasks(task_id,official_request_id,task_key,task_type,worker_type,task_input,result_ref,
                        updated_at,depends_on_task_id,status,retry_count) VALUES(?,?,?,?,?,?,NULL,1,NULL,'pending',0)""",
                       (row['task_id'],oid,row['task_key'],row['task_type'],row['worker_type'],row['task_input']))
        claim=self.mark('prop_pack_publish',10)
        artifact=self.api('/prepare',{'claim':claim})['manifest']
        result=self.api('',{'claim':claim,'manifest':artifact})
        self.mark('prop_pack_publish',10,result['resultRef'])
        proof=self.api('/read',self.pin(artifact))
        self.assertEqual(proof['prior'],prior)
        self.assertEqual(proof['receipt']['members'],[])
        self.assertEqual([v['scalePercent'] for v in artifact['scaleVariants']],[5])

    def run_scale(self,n,stamp):
        key=f'prop_scale_{n:03d}'
        if self.worker_modules():
            r=self.run_postprocess(key)
        else:
            claim=self.mark(key,stamp)
            r=self.api('/derive-scale',{'claim':claim})
            self.mark(key,stamp,r['resultRef'])
        self.assertTrue(r['resultRef'].startswith('prop.scale-pair.'))

    def worker_modules(self):
        return os.getenv('PROP_POSTPROCESS_MODULE_DIR')

    def run_postprocess(self,key):
        package=self.worker_modules()
        cmd="""import {pathToFileURL} from 'node:url';import {join} from 'node:path';
const [root,dbPath,officialRequestId,taskId,base]=process.argv.slice(1);
const imp=name=>import(pathToFileURL(join(root,name)).href);
const {propStagePorts}=await imp('prop-stage-ports.mjs');
const {nextPropPackageFromPath,runPropPackageFromPath}=await imp('prop-package-worker.mjs');
const pds=propStagePorts({transport:async(path,{body,timeout})=>{
 const r=await fetch(base+path,{method:'POST',body:JSON.stringify(body),headers:{'Content-Type':'application/json'},signal:AbortSignal.timeout(timeout)});
 if(!r.ok)throw Error('HTTP '+r.status);
 return Buffer.from(await r.arrayBuffer());
}});
const pick=nextPropPackageFromPath(dbPath);
if(pick?.officialRequestId!==officialRequestId||pick?.taskId!==taskId)throw Error('unexpected eligible task');
console.log(JSON.stringify(await runPropPackageFromPath({dbPath,officialRequestId,taskId,pds})));
"""
        out=subprocess.run(['node','--input-type=module','-e',cmd,package,str(self.db),self.oid,task_id(self.oid,key),self.pds],
                           cwd=REPO,capture_output=True)
        self.assertEqual(out.returncode,0,out.stderr.decode())
        reply=json.loads(out.stdout)
        self.assertEqual(reply['state'],'completed',reply)
        return reply

    def exercise_three_static_assets(self,profile,scales=None):
        self.register_generate(self.db,profile=profile,scales=scales)
        d=self.mark('prop_design',2)
        self.assertEqual(self.stage(DESIGN_ROUTE,'/read',{'claim':d})['input']['presentationProfile'],profile)
        doc={'schemaVersion':'ngs.prop-design-spec.v1','propId':PROP,'designContextSha256':DESIGN_HASH,
             'title':'등롱','appearancePrompt':'종이 등롱','motion':'still','motionPrompt':None,
             'interaction':{'pixelOpen':None,'detailTrigger':None}}
        r=self.stage(DESIGN_ROUTE,'',{'claim':d,'artifact':doc})
        self.mark('prop_design',2,r['resultRef'])
        original,raw=artifact_store(31,29,8)
        img=self.mark('prop_base',3)
        r=self.stage(STAGE,'',{'claim':img,'artifact':{'media':{k:original[k] for k in
            ('sha256','bytes','width','height','mediaType')},'base64':base64.b64encode(raw).decode()}})
        self.mark('prop_base',3,r['resultRef'])
        v2=profile=='spirit-v2'
        stages=[('prop_detail',31,29)]
        if v2:stages.extend([('prop_pixel_source',31,29),('prop_pixel_detail',31,29)])
        stages.extend([('prop_pixel',6,6),('prop_spirit',6,6)])
        parent={'prop_pixel_source':'prop_detail','prop_pixel_detail':'prop_pixel_source','prop_spirit':'prop_detail',
                'prop_pixel':'prop_pixel_detail' if v2 else 'prop_detail'}
        refs={};assets={};next_stamp=len(stages)+4
        for i,(key,w,h) in enumerate(stages,4):
            claim=self.mark(key,i)
            stage=self.stage(STAGE,'/read',{'claim':claim})
            self.assertEqual(stage['canvas'],{'width':w,'height':h})
            if key in parent:
                self.assertEqual(stage['source'],{'taskId':task_id(self.oid,parent[key]),'resultRef':refs[parent[key]]})
                asset=self.stage(STAGE,'/asset',{'claim':claim,'purpose':'input','index':None},raw=True)
                self.assertEqual(asset,assets[parent[key]])
                self.assertEqual(sha(asset),stage['media']['sha256'])
            media,raw=artifact_store(w,h,i+35)
            if key=='prop_pixel_source':
                self.assertEqual(set(stage),{'design','context','source','media','canvas'})
                self.assertEqual(set(stage['media']),{'sha256','bytes','width','height','mediaType'})
                self.assertEqual(stage['design']['artifact'],doc)
                bad,b=artifact_store(30,29,i+35)
                bad={k:bad[k] for k in ('sha256','bytes','width','height','mediaType')}
                self.stage(STAGE,'',{'claim':claim,'artifact':{'media':bad,'base64':base64.b64encode(b).decode()}},403)
                media={k:media[k] for k in ('sha256','bytes','width','height','mediaType')}
            if key=='prop_spirit':
                self.api('/prepare',{'claim':self.mark('prop_pack_publish',next_stamp)},expected=403)
            r=self.stage(STAGE,'',{'claim':claim,'artifact':{'media':media,'base64':base64.b64encode(raw).decode()}})
            self.mark(key,i,r['resultRef']);refs[key]=r['resultRef'];assets[key]=raw
        self.assertTrue(r['resultRef'].startswith('prop.spirit-rgba.'))
        if scales:
            for n in sorted(scales):
                self.run_scale(n,next_stamp);next_stamp+=1
                self.assertIsNone(self.filex_writer(WRITER+'/head',{'propId':PROP})['pointer'])
        final=self.mark('prop_pack_publish',next_stamp)
        pack=self.api('/prepare',{'claim':final})['manifest']
        if v2:
            key='prop_pixel_source'
            path=self.actual.staging.root/self.oid/task_id(self.oid,key)/refs[key]/'asset.png'
            path.write_bytes(assets['prop_detail'])
            self.api('',{'claim':final,'manifest':pack},expected=403)
            path.write_bytes(assets[key])
        if scales:
            path=self.actual.staging.root/self.oid/task_id(self.oid,f'prop_scale_{min(scales):03d}')/'pixel.png'
            exact=path.read_bytes()
            path.write_bytes(exact[:-1]+b'!')
            self.api('',{'claim':final,'manifest':pack},expected=403)
            self.assertIsNone(self.filex_writer(WRITER+'/head',{'propId':PROP})['pointer'])
            path.write_bytes(exact)
        self.assertEqual(set(pack['media']),{'detail','pixel','spirit'})
        self.assertEqual(pack['media']['spirit'],media)
        invalid={**pack,'media':{**pack['media'],'spirit':pack['media']['pixel']}}
        self.api('',{'claim':final,'manifest':invalid},expected=403)
        published=self.api('',{'claim':final,'manifest':pack})
        self.mark('prop_pack_publish',next_stamp,published['resultRef'])
        self.assertEqual(self.api('/read',self.pin(pack))['manifest'],pack)
        actual=self.api('/asset',{'pin':self.pin(pack),'assetId':media['assetId']})
        self.assertEqual(base64.b64decode(actual['base64']),raw)
        return pack,assets.get('prop_pixel_detail')

    def test_live_lease_and_append_uses_exact_original_preserves_static_metadata_and_reconciles(self):
        self.exercise_append(PROP)

    def test_chroma_profile_reaches_only_exact_raw_and_detail_claims(self):
        self.exercise_append(CANDLE_CHROMA_PROP,CANDLE_CHROMA_PROFILE)

    def exercise_append(self,prop_id,profile=None):
        from project_data_service.prop_animation_ready import SCHEMA,PROVIDER,renderer_ready
        lease=Path(self.temp.name).resolve()/'lease'; lease.mkdir(mode=0o700)
        marker=lease/'ready.json'
        self.actual.append_readiness=lambda:renderer_ready(marker)
        def modes():
            with urlopen(self.pds+PATH,timeout=3) as response:return json.load(response)['modes']
        self.assertEqual(modes(),['still'])
        def signal(t):
            marker.write_bytes(wire({'schemaVersion':SCHEMA,'provider':PROVIDER,'checkedAt':t}))
            marker.chmod(0o600)
        now=time.time_ns()//1_000_000
        signal(now-121_000);self.assertEqual(modes(),['still'])
        signal(now+10_000);self.assertEqual(modes(),['still'])
        signal(now)
        marker.chmod(0o644);self.assertEqual(modes(),['still'])
        marker.chmod(0o600);self.assertEqual(modes(),['still','append-animation'])
        fixture=make('a',prop_id=prop_id)
        prior=fixture['manifest']
        self.filex_writer(WRITER,{k:fixture[k] for k in ('candidate','manifest','media')})
        source={k:v for k,v in self.pin(prior).items() if k!='propId'}
        oid='request.official.'+'d'*64
        payload={'schemaVersion':'ngs.prop-animation-generate.v1','projectId':'binari','propId':prop_id,
                 'source':source,'motionId':'gentle-flicker','prompt':'Only the flame flickers'}
        legacy_sha=digest(payload)
        if profile:payload['renderProfile']=profile
        additions={'renderProfile':profile} if profile else {}
        subprocess.run(['node','--input-type=module','-e',"""import {PlannerStore} from './workflow-planner/planner.mjs';
import {canonical,sha} from './workflow-planner/envelope.mjs';
const [db,id,raw]=process.argv.slice(1),payload=JSON.parse(raw);
const s=new PlannerStore(db,{propGenerateEnabled:true,propAnimationEnabled:true,propMutationsEnabled:true,readPropPublication:async()=>null});
s.stage({officialRequestId:id,requestType:'prop.animation.generate',projectId:'binari',schemaVersion:payload.schemaVersion,
 payload,payloadSha256:sha(canonical(payload)),status:'registered',registeredAt:2});
s.acknowledge(id);s.close();
""",str(self.db),oid,json.dumps(payload)],cwd=REPO,check=True,capture_output=True)
        self.oid=oid; self.payload_sha=digest(payload)
        motion=self.mark('prop_motion',10)
        self.assertEqual(self.stage(STAGE,'/read',{'claim':motion}),{'package':source,'media':prior['media']['detail'],
                         'motionPrompt':payload['prompt'],**additions})
        if profile:
            self.stage(STAGE,'/read',{'claim':{**motion,'payloadSha256':legacy_sha}},expected=403)
        raw=self.stage(STAGE,'/asset',{'claim':motion,'purpose':'input','index':None},raw=True)
        self.assertEqual(sha(raw),prior['media']['detail']['sha256'])
        def rgb(color):
            def c(k,v):return struct.pack('!I',len(v))+k+v+struct.pack('!I',zlib.crc32(k+v))
            return b'\x89PNG\r\n\x1a\n'+c(b'IHDR',struct.pack('!IIBBBBB',32,32,8,2,0,0,0))+c(
                b'IDAT',zlib.compress((b'\0'+bytes([color,20,30])*32)*32))+c(b'IEND',b'')
        frames=[rgb(50),rgb(60)]
        m={'schemaVersion':'ngs.prop-native-rgb-sequence.v1','officialRequestId':oid,'taskId':motion['taskId'],'propId':prop_id,
           'width':32,'height':32,'frameDurationsMs':[100,100],
           'frames':[{'index':i,'sha256':sha(b),'bytes':len(b),'mediaType':'image/png'} for i,b in enumerate(frames)]}
        raw_art={'manifest':m,'frames':[base64.b64encode(b).decode() for b in frames]}
        result=self.stage(STAGE,'',{'claim':motion,'artifact':raw_art})
        self.mark('prop_motion',10,result['resultRef'])
        marker.unlink();self.assertEqual(modes(),['still'])
        clips={}
        for n,lane,w in [(11,'detail',32),(12,'pixel',16)]:
            key='prop_animation_'+lane;cl=self.mark(key,n)
            inputs=self.stage(STAGE,'/read',{'claim':cl})
            self.assertEqual(inputs['geometry'],{**source,'media':prior['media'][lane]})
            if profile and lane=='detail':self.assertEqual(inputs['renderProfile'],profile)
            else:self.assertNotIn('renderProfile',inputs)
            self.assertEqual(sha(self.stage(STAGE,'/asset',{'claim':cl,'purpose':'geometry','index':None},raw=True)),
                             prior['media'][lane]['sha256'])
            meta,data=artifact_store(w,w,100+n,True); clips[lane]=meta
            r=self.stage(STAGE,'',{'claim':cl,'artifact':{'media':meta,'base64':base64.b64encode(data).decode()}})
            self.mark(key,n,r['resultRef'])
        publish=self.mark('prop_pack_publish',13)
        artifact=self.api('/prepare',{'claim':publish})['manifest']
        self.assertEqual(artifact['media'],prior['media'])
        self.assertEqual(artifact['interaction'],prior['interaction'])
        self.assertEqual(artifact['animations'],[{'id':payload['motionId'],'title':payload['motionId'],
            'officialRequestId':oid,'requestSha256':self.payload_sha,'media':clips}])
        out=self.api('',{'claim':publish,'manifest':artifact})
        self.assertEqual(self.api('/prepare',{'claim':publish})['manifest'],artifact)
        self.assertEqual(self.api('',{'claim':publish,'manifest':artifact}),out)
        self.assertEqual(self.filex_writer(WRITER+'/read',self.pin(prior))['manifest'],prior)
        self.mark('prop_pack_publish',13,out['resultRef'])
        self.assertEqual(self.api('/read',self.pin(artifact))['manifest'],artifact)

if __name__=='__main__':main()
