"""Actual Planner-created SQLite -> read-only PDS stages -> authenticated Filex."""
import base64,json,os,sqlite3,subprocess
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from unittest import main,TestCase
from test_prop_package_http import PropFixture,PROP,artifact_store

from prop_package_contract import wire,digest,sha
from project_data_service.prop_design_context import PropDesignContext
from project_data_service.prop_stage_claim import PropClaimAuthority,task_id,build_graph
from project_data_service.prop_stage_store import PropStageStore
from project_data_service.workflow_delete_claim import PrivatePlannerClaim
from project_data_service.workflow_prop_package import WorkflowPropPackage,bound_prop_writer
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
        blob={
          'schemaVersion':'ngs.prop-generate.v1','projectId':'binari','propId':PROP,
          'intent':'테스트 등롱','designContextSha256':DESIGN_HASH}
        self.oid='request.official.'+'b'*64
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
        stat=db.stat()
        self.db=db;self.payload=blob;self.payload_sha=digest(blob)
        stage=parent/'prop-stage';stage.mkdir(mode=0o700)
        actual=RealPropAuthority(PropClaimAuthority(PrivatePlannerClaim(str(db),stat.st_dev,stat.st_ino)),
                                 PropStageStore(stage),Context(),self.filex_writer)
        self.pds=self.launch(self.handler,workflow_prop_package=WorkflowPropPackage(actual,self.filex_writer),
                             workflow_prop_stage=actual)
        self.actual=actual

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
            ('prop.regenerate',{'source':source,'motion':'subtle'},'ngs.prop-regenerate.v1'),
            ('prop.animation.generate',{'source':source,'motionId':'gentle-sway','prompt':'one motion'},'ngs.prop-animation-generate.v1')):
            p={**self.payload,**extra,'schemaVersion':schema}
            if kind=='prop.animation.generate':
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
        r=self.stage(STAGE,'',{'claim':img,'artifact':{'media':base_record,'base64':base64.b64encode(original).decode()}})
        self.mark('prop_base',3,r['resultRef'])
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

if __name__=='__main__':main()
