"""Actual sterile PDS -> authenticated Filex HTTP -> filesystem pointer/byte readback."""
import base64,copy,json,os,struct,tempfile,threading,unittest,zlib
from contextlib import contextmanager
import sqlite3
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen

from prop_package_contract import digest,sha,wire
from prop_package_store import PropPackageStore,Conflict
from concept_store import ConceptStore
from server import handler
from project_data_service.filex_cas_transport import FilexCASTransport
from project_data_service.http_server import Handler as ProductionPDS

class PDS(ProductionPDS):
    def log_message(self,*args):pass
from project_data_service.workflow_prop_package import PATH,WorkflowPropPackage,bound_prop_writer,Denied

PROP='lantern'
def png(w,h,color,animated=False):
    def chunk(k,data):return struct.pack('!I',len(data))+k+data+struct.pack('!I',zlib.crc32(k+data))
    def zframe(r):
        row=b'\0'+bytes([r,2,3,255])*(w-1)+b'\0\0\0\0'
        return zlib.compress(row*h)
    raw=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!IIBBBBB',w,h,8,6,0,0,0))
    if animated:
        raw+=chunk(b'acTL',struct.pack('!II',2,1))
        frame=lambda s:struct.pack('!IIIIIHHBB',s,w,h,0,0,100,1000,0,0)
        raw+=chunk(b'fcTL',frame(0))+chunk(b'IDAT',zframe(color))
        raw+=chunk(b'fcTL',frame(1))+chunk(b'fdAT',struct.pack('!I',2)+zframe(color+1))
    else:raw+=chunk(b'IDAT',zframe(color))
    return raw+chunk(b'IEND',b'')

def artifact_store(w,h,color,animated=False):
    raw=png(w,h,color,animated);s=sha(raw)
    return {'assetId':s+'.png','sha256':s,'bytes':len(raw),'width':w,'height':h,
            'mediaType':'image/apng' if animated else 'image/png','frameDurationsMs':[100,100] if animated else []},raw

class FixtureAuthority:
    """Trusted fixture port. Uses only pre-admitted operation/bytes, never caller input as authority."""
    def __init__(self):
        self.claims={};self.state=threading.local();self.calls=0
    @contextmanager
    def snapshot(self,claim):
        entry=self.claims.get(claim.get('officialRequestId'))
        if entry is None or claim!=entry['claim']:raise Denied()
        self.state.in_scope=True
        try:
            def prepare(manifest,prior,head,*,replayed=False):
                if manifest!=entry['manifest'] or prior!=entry['prior'] or head!=entry['candidate']['scope'].get('source'):raise Denied()
                return entry['candidate']
            yield prepare
        finally:self.state.in_scope=False
    def read_media(self,claim,c,m,prior):
        assert self.state.in_scope
        self.calls+=1
        return self.claims[claim['officialRequestId']]['media']
    def authorize_read(self,operation,packet):
        p=packet['pin'] if operation=='asset' else packet
        if p['propId']!=PROP:raise Denied()

def make(id_char,operation='create',prior=None,color=20,*,prop_id=PROP,motion_id='gentle-sway'):
    oid='request.official.'+id_char*64
    request_sha=digest(['formal',oid])
    scope={'officialRequestId':oid,'projectId':'binari','propId':prop_id,'requestSha256':request_sha}
    m={'schemaVersion':'ngs.prop-package.v1',**scope,'revision':1,
       'designContextSha256':'f'*64,'title':'등롱','parentSha256':None,
       'interaction':{'pixelOpen':None,'detailTrigger':None},'animations':[]}
    raws={}; members=[]
    def ref(name,prefix,d=None):
        members.append({'key':name,'taskId':'workflow.task.'+digest([oid,name]),'ref':'prop.'+prefix+'.'+(d or 'e'*64)})
    def pair(animated=False):
        parts={}
        for lane,w in [('detail',32),('pixel',16)]:
            parts[lane],raw=artifact_store(w,w,color,animated)
            raws[parts[lane]['assetId']]=base64.b64encode(raw).decode()
        return parts
    def media_refs(group,animated):
        for lane in ('detail','pixel'):
            ref(('prop_animation_' if animated else 'prop_')+lane,('animation-' if animated else '')+lane+'-rgba',group[lane]['sha256'])
    if prior:
        source={'revision':prior['revision'],'sha256':digest(prior)}
        m.update(revision=prior['revision']+1,parentSha256=source['sha256'])
        scope.update(operation=operation,source=source)
    if operation=='append-animation':
        m.update(title=prior['title'],media=copy.deepcopy(prior['media']),interaction=copy.deepcopy(prior['interaction']),
                 designContextSha256=prior['designContextSha256'])
        group=pair(True);motion=motion_id
        m['animations']=copy.deepcopy(prior['animations'])+[{'id':motion,'title':'흔들림','officialRequestId':oid,'requestSha256':request_sha,'media':group}]
        scope['animationId']=motion;ref('prop_motion','i2v-rgb');media_refs(group,True)
    else:
        m['media']=pair();scope.update(motion='still',designContextSha256=m['designContextSha256'])
        ref('prop_design','design-spec');ref('prop_base','base-png');media_refs(m['media'],False)
    d=digest(m)
    c={'scope':scope,'members':members,'manifestSha256':d,'resultRef':'prop.package.'+d}
    claim={'officialRequestId':oid,'projectId':'binari','taskId':'workflow.task.'+digest([oid,'prop_pack_publish']),
           'payloadSha256':request_sha,'claimStamp':1}
    return {'claim':claim,'manifest':m,'candidate':c,'prior':prior,'media':raws}

class PropFixture:
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        p=Path(self.temp.name).resolve();(p/'props').mkdir();(p/'unrelated').mkdir()
        self.props=PropPackageStore(p/'props')
        self.legacy=(p/'props'/'legacy-lamp');self.legacy.mkdir();(self.legacy/'old.png').write_bytes(b'original')
        token='test-only-token-'+'h'*48;key=p/'token'
        key.write_text(token);key.chmod(0o600)
        dummy=ConceptStore(p/'unrelated')
        filex=self.launch(handler(dummy,token,workflow_props=self.props))
        config={'schemaVersion':'pds-filex-cas.v1','projectId':'binari','enabled':True,'endpoint':filex,'tokenFile':str(key)}
        self.delete_config={**config,'endpoint':self.launch(handler(self.props,token,workflow_props=self.props))}
        self.authority=FixtureAuthority()
        self.filex_writer=bound_prop_writer(lambda:FilexCASTransport(config));self.handler=PDS
        self.pds=self.launch(PDS,workflow_prop_package=WorkflowPropPackage(self.authority,self.filex_writer))
    def launch(self,h,**kw):
        s=ThreadingHTTPServer(('127.0.0.1',0),h)
        for k,v in kw.items():setattr(s,k,v)
        worker=threading.Thread(target=s.serve_forever,daemon=True);worker.start()
        def close():
            s.shutdown();s.server_close();worker.join(timeout=2)
        self.addCleanup(close);return 'http://127.0.0.1:'+str(s.server_port)
    def api(self,route,body,headers=None,expected=200):
        req=Request(self.pds+PATH+route,data=wire(body),headers={'Content-Type':'application/json',**(headers or {})},method='POST')
        try:
            with urlopen(req,timeout=7) as r:self.assertEqual(r.status,expected);return json.load(r)
        except HTTPError as e:
            with e:self.assertEqual(e.code,expected);return json.load(e)
    def admit(self,e):
        self.authority.claims[e['claim']['officialRequestId']]=e
    def publish(self,e,expected=200):
        self.admit(e)
        return self.api('',{k:e[k] for k in ('claim','manifest')},expected=expected)
    def current(self):return self.api('/head',{'propId':PROP})['pointer']
    def pin(self,m):return {'propId':m['propId'],'revision':m['revision'],'sha256':digest(m)}

class PropHTTP(PropFixture,unittest.TestCase):
    def test_three_real_publications_pinned_history_replay_and_unchanged_legacy(self):
        original=make('a')
        self.assertIsNone(self.current())
        self.assertEqual(self.publish(original),{'ok':True,'resultRef':original['candidate']['resultRef']})
        a=original['manifest'];self.assertEqual({k:self.current()[k] for k in self.pin(a)},self.pin(a))
        self.assertEqual(self.publish(original)['resultRef'],original['candidate']['resultRef'])
        self.assertEqual(self.authority.calls,1)
        append=make('b','append-animation',a,color=50);self.publish(append)
        b=append['manifest'];proof=self.api('/read',self.pin(b));self.assertEqual(proof['prior'],a)
        self.assertEqual(proof['manifest']['media'],a['media'])
        self.assertEqual(len(proof['manifest']['animations']),1)
        regen=make('c','regenerate',b,color=80)
        with self.assertRaises(Conflict):
            self.props.publish({k:regen[k] for k in ('candidate','manifest','media')})
        self.assertEqual(self.current()['revision'],2)
        # Install an exact pre-retirement fixture to test durable read compatibility.
        c=regen['manifest'];path=self.props.root/c['propId']
        for name,value in regen['media'].items():
            self.props.write(path/'assets'/name,base64.b64decode(value))
        self.props.write(path/'releases'/'3'/'manifest.json',wire(c))
        self.props.write(path/'releases'/'3'/'.receipt.json',wire({'schemaVersion':'ngs.prop-package-publication-receipt.v1',
         'authority':'filex','status':'committed',**regen['candidate']}))
        self.props.write(path/'metadata.json',wire({'schemaVersion':'ngs.prop-pointer.v1','projectId':'binari','title':c['title'],**self.pin(c)}))
        self.assertEqual(self.current()['revision'],3)
        self.assertEqual(self.api('/read',self.pin(c))['manifest']['animations'],[])
        self.assertEqual(self.api('/read',self.pin(b))['manifest'],b)
        asset=b['animations'][0]['media']['pixel']['assetId']
        read=self.api('/asset',{'pin':self.pin(b),'assetId':asset})
        self.assertEqual(sha(base64.b64decode(read['base64'])),asset[:-4])
        self.api('/asset',{'pin':self.pin(c),'assetId':asset},expected=404)
        self.assertEqual((self.legacy/'old.png').read_bytes(),b'original')
        self.publish(make('d','regenerate',a,color=34),expected=409)
        self.assertEqual(self.current()['revision'],3)
        self.api('/head',{'propId':PROP},headers={'Origin':'https://foreign.example'},expected=403)
    def test_pinned_folder_quarantines_animations_preserves_legacy_and_rejects_stale(self):
        from single_target_delete import PinnedDeleteRejected
        a=make('a');self.publish(a)
        b=make('b','append-animation',a['manifest'],color=50);self.publish(b)
        def command(m):
            pin=self.pin(m)
            return {'operationId':'workflow.task.'+'c'*64,
                    'target':{'root':'canonical-files/binari/props','path':PROP,'kind':'directory'},
                    'expected':{k:pin[k] for k in ('revision','sha256')}}
        with self.assertRaises(PinnedDeleteRejected):self.props.remove_package(command(a['manifest']))
        self.assertEqual(self.current()['revision'],2)
        receipt=self.props.remove_package(command(b['manifest']))
        self.assertTrue(receipt['targetAbsent'])
        self.assertIsNone(self.current())
        self.assertTrue((self.props.root/'.storage-quarantine'/receipt['recoveryId']/'target'/'releases'/'2'/'manifest.json').is_file())
        self.assertEqual(self.props.remove_package(command(b['manifest'])),receipt)
        self.assertEqual((self.legacy/'old.png').read_bytes(),b'original')
        self.publish(make('d',color=65))
        with self.assertRaises(PinnedDeleteRejected):self.props.remove_package(command(b['manifest']))
        self.assertEqual(self.current()['revision'],1)

    def test_standalone_delete_authority_http_binds_task_target_and_pin(self):
        from project_data_service.single_target_delete import SingleTargetDelete,PATH as delete_path
        from project_data_service.workflow_file_delete import PROP_ROOT, storage_provider
        class Planner:
            @contextmanager
            def snapshot(own):
                yield db
        db=sqlite3.connect(':memory:',check_same_thread=False);db.row_factory=sqlite3.Row
        self.addCleanup(db.close)
        db.execute('CREATE TABLE workflow_runs(official_request_id,project_id,request_type,schema_version,state,reason,payload,payload_sha256,registered_at)')
        db.execute('CREATE TABLE workflow_tasks(official_request_id,task_id,task_key,task_type,worker_type,depends_on_task_id,task_input,status,result_ref,updated_at)')
        transport=lambda:FilexCASTransport(self.delete_config)
        svc=SingleTargetDelete({PROP_ROOT:storage_provider(Planner(),transport,lambda:True)})
        endpoint=self.launch(PDS,single_target_delete=svc)+delete_path
        a=make('a');self.publish(a)
        b=make('b','append-animation',a['manifest'],color=54);self.publish(b)
        def claim(label,manifest):
            oid='request.official.'+label*64
            task='workflow.task.'+digest([oid,'file_delete'])
            target={'root':PROP_ROOT,'path':PROP,'kind':'directory'}
            expected={k:v for k,v in self.pin(manifest).items() if k!='propId'}
            data=wire({'schemaVersion':'ngs.file-delete.v1','projectId':'binari','target':target,'expected':expected})
            db.execute('INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,?,?,?)',
                (oid,'binari','file.delete','ngs.file-delete.v1','planned',None,data.decode(),sha(data),1))
            db.execute('INSERT INTO workflow_tasks VALUES(?,?,?,?,?,?,?,?,?,?)',
                (oid,task,'file_delete','file.delete','file',None,wire({'target':target,'expected':expected}).decode(),'running',None,2))
            db.commit()
            return {'target':target,'claim':{'officialRequestId':oid,'taskId':task,'payloadSha256':sha(data),
                                           'projectId':'binari','claimStamp':2}}
        def send(packet,status):
            req=Request(endpoint,data=wire(packet),headers={'Content-Type':'application/json'},method='POST')
            try:
                with urlopen(req,timeout=7) as r:self.assertEqual(r.status,status);return json.load(r)
            except HTTPError as e:
                with e:self.assertEqual(e.code,status);return json.load(e)
        stale=claim('c',a['manifest'])
        self.assertEqual(send(stale,409),{'error':'STORAGE_DELETE_STALE_TARGET'})
        live=claim('d',b['manifest'])
        self.assertEqual(send({**live,'target':{**live['target'],'path':'legacy-lamp'}},403),
                         {'error':'STORAGE_DELETE_CLAIM_DENIED'})
        done=send(live,200)
        self.assertTrue(done['targetAbsent'])
        self.assertEqual(send(live,200),done)
        self.assertIsNone(self.current())
        self.assertTrue((self.legacy/'old.png').is_file())

    def test_interrupted_atomic_head_is_invisible_then_exact_retry_and_cas(self):
        a=make('a');self.publish(a)
        b=make('b','append-animation',a['manifest'],color=50)
        real=self.props.write
        original_inode=(self.props.root/PROP/'metadata.json').stat().st_ino
        def crash(path,data):
            if path.name=='metadata.json':raise OSError('injected before atomic pointer')
            return real(path,data)
        self.props.write=crash
        self.publish(b,expected=503)
        self.assertEqual(self.current()['revision'],1)
        self.assertEqual((self.props.root/PROP/'metadata.json').stat().st_ino,original_inode)
        self.api('/read',self.pin(b['manifest']),expected=404)
        self.props.write=real
        self.publish(b)
        self.assertEqual(self.current()['revision'],2)
        c=make('c','append-animation',b['manifest'],color=60,motion_id='new-sway')
        wrong=copy.deepcopy(c);key=next(iter(wrong['media']));wrong['media'][key]='!'*len(wrong['media'][key])
        self.publish(wrong,expected=503)
        self.assertEqual(self.current()['revision'],2)
        first=make('d','append-animation',b['manifest'],color=70,motion_id='new-sway')
        second=make('e','append-animation',b['manifest'],color=90,motion_id='new-sway')
        def race(e,results):
            self.admit(e)
            req=Request(self.pds+PATH,data=wire({k:e[k] for k in ('claim','manifest')}),
                        headers={'Content-Type':'application/json'},method='POST')
            try:
                with urlopen(req,timeout=5) as r:results.append(r.status)
            except HTTPError as exc:
                with exc:results.append(exc.code)
        outcomes=[];threads=[threading.Thread(target=race,args=(e,outcomes)) for e in (first,second)]
        for x in threads:x.start()
        for x in threads:x.join(timeout=7)
        self.assertEqual(outcomes.count(200),1);self.assertEqual(self.current()['revision'],3)

if __name__=='__main__':unittest.main()
