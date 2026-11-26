"""Existing PDS loopback boundary for prop-only, claim-bound intermediate receipts."""
import base64
from contextlib import contextmanager
from urllib.parse import urlsplit

from .filex_read import digest
from .prop_package_contract import sha
from .prop_design_context import PropDesignContext
from .prop_stage_claim import (
    APPEND,BASE,DESIGN,DETAIL,GENERATE,MOTION,MOTION_DETAIL,MOTION_PIXEL,PIXEL,PUBLISH,REGENERATE,
    PropClaimAuthority,ref_name,task_id,valid_ref,
)
from .prop_stage_store import PropStageStore,STAGES
from .workflow_motion_2d import strict_json
from .workflow_prop_package import Denied,Stale,WRITER,keys,need

STAGE='/v1/props/ownership/workflow-stage'
DESIGN_ROUTE='/v1/props/ownership/workflow-design'
ROUTES=(STAGE,STAGE+'/read',STAGE+'/asset',DESIGN_ROUTE,DESIGN_ROUTE+'/read')
MAX_BODY=57*1024*1024
MAX_IMAGE_REFERENCE=8*1024*1024

class Waiting(ValueError):pass
class ReferenceChanged(ValueError):pass
def _source(t):return {k:t[k] for k in ('taskId','resultRef')}
def _pointer(p):return {'revision':p['revision'],'sha256':p['sha256']} if p else None

def rect(r,canvas,action):
    if r is None:return None
    x=r['xPermille']*canvas['width']//1000;y=r['yPermille']*canvas['height']//1000
    far_x=((r['xPermille']+r['widthPermille'])*canvas['width']+999)//1000
    far_y=((r['yPermille']+r['heightPermille'])*canvas['height']+999)//1000
    return {'action':action,'x':x,'y':y,'width':far_x-x,'height':far_y-y}

def expected_interaction(design,media):
    return {'pixelOpen':rect(design['interaction']['pixelOpen'],media['pixel'],'open'),
            'detailTrigger':rect(design['interaction']['detailTrigger'],media['detail'],'activate')}

def canvas_of(m):return {k:m[k] for k in ('width','height')}
def half_canvas(m):
    # Same integer half-size policy as the staged postprocess port (max 512).
    w,h=m['width'],m['height'];den=max(2,max(w,h)/512)
    return {'width':max(12,int(w/den+0.5)),'height':max(12,int(h/den+0.5))}

class RealPropAuthority:
    """Private Planner authority injected into WorkflowPropPackage and every stage."""
    def __init__(self,planner,staging,concept,writer,*,motion_enabled=False,append_readiness=None):
        need(isinstance(planner,PropClaimAuthority) and isinstance(staging,PropStageStore) and
             isinstance(concept,PropDesignContext) and callable(writer))
        self.planner,self.staging,self.concept,self.writer=planner,staging,concept,writer
        self.motion_enabled=motion_enabled is True
        self.append_readiness=append_readiness

    def require_mode(self,run,p):
        if run['request_type']==APPEND:
            # Production append jobs are admitted only with live readiness.
            # A later idle/probe expiry must not prevent an already-rendered
            # job from completing its verified CPU assets and publication.
            need(self.motion_enabled or callable(self.append_readiness))
        else:need(self.motion_enabled or p.get('motion','still')=='still')

    def package_modes(self):
        modes=['still']
        if callable(self.append_readiness) and self.append_readiness() is True:modes.append('append-animation')
        return modes

    def readiness(self):
        with self.planner.claim.snapshot():pass
        self.staging._root()
        return callable(getattr(self.writer,'ready',None)) and self.writer.ready() is True and bool(self.concept.read('binari'))

    def stored(self,run,rows,key):
        r=rows[key]
        if r['status']!='completed':raise Waiting()
        need(valid_ref(key,r['result_ref']))
        return self.staging.get(run['official_request_id'],r['task_id'],key,r['result_ref'])

    def current_source(self,p,kind):
        if kind not in (APPEND,REGENERATE):return None
        head=self.writer(WRITER+'/head',{'propId':p['propId']})['pointer']
        if _pointer(head)!=p['source']:raise Stale()
        prior=self.writer(WRITER+'/read',{'propId':p['propId'],**p['source']})['manifest']
        if kind==APPEND and any(a['id']==p['motionId'] for a in prior['animations']):raise Stale()
        return prior

    def context(self,p,kind):
        self.current_source(p,kind)
        context=self.concept.read('binari')
        if context['contextSha256']!=p['designContextSha256']:raise Stale()
        return context

    @contextmanager
    def snapshot(self,claim):
        with self.planner.selected(claim,(PUBLISH,)) as (run,p,rows,key):
            self.require_mode(run,p)
            # The DB shared read transaction spans all Filex read/write operations.
            members=[];records={}
            for k,r in rows.items():
                if k==PUBLISH:continue
                a=self.stored(run,rows,k);records[k]=a
                members.append({'key':k,'taskId':a['taskId'],'ref':a['resultRef']})
            kind=run['request_type']
            def prepare(m,prior,head,*,replayed=False):
                need(m['propId']==p['propId'] and m['officialRequestId']==run['official_request_id'] and
                     m['requestSha256']==run['payload_sha256'] and head==p.get('source'))
                scope={'officialRequestId':m['officialRequestId'],'projectId':'binari','propId':m['propId'],'requestSha256':m['requestSha256']}
                def source(k):return _source(records[k])
                for k,record in records.items():
                    line=record['lineage']
                    if k==DESIGN:need(line is None)
                    elif k==BASE:need(line=={'design':source(DESIGN)})
                    elif k in (DETAIL,PIXEL):need(line=={'source':source(BASE if k==DETAIL else DETAIL)})
                    elif k==MOTION:
                        need(line==({'package':p['source']} if kind==APPEND else {'source':source(BASE)}))
                    elif k in (MOTION_DETAIL,MOTION_PIXEL):
                        lane='detail' if k==MOTION_DETAIL else 'pixel'
                        dep=MOTION if k==MOTION_DETAIL else MOTION_DETAIL
                        geom=DETAIL if k==MOTION_DETAIL else PIXEL
                        geometry={**p['source'],'media':prior['media'][lane]} if kind==APPEND else {
                            **source(geom),'media':records[geom]['artifact']}
                        need(line=={'source':source(dep),'geometry':geometry})
                if kind==APPEND:
                    clips=m.get('animations')
                    need(prior is not None and type(clips)is list and bool(clips) and type(clips[-1])is dict and
                         clips[-1].get('title')==p['motionId'])
                    scope.update(operation='append-animation',source=p['source'],animationId=p['motionId'])
                else:
                    # A superseded design pin must not silently become a published bundle.
                    if not replayed:
                        actual=self.concept.read('binari')
                        if actual['contextSha256']!=p['designContextSha256']:raise Stale()
                    mode=p.get('motion','still')
                    scope.update(designContextSha256=p['designContextSha256'],motion=mode)
                    design=self.stored(run,rows,DESIGN)['artifact']
                    need(m['title']==design['title'] and m['interaction']==expected_interaction(design,m['media']))
                    if kind==REGENERATE:scope.update(operation='regenerate',source=p['source'])
                d=digest(m);return {'scope':scope,'members':members,'manifestSha256':d,'resultRef':ref_name(PUBLISH,d)}
            yield prepare

    def prepare_manifest(self,claim):
        with self.planner.selected(claim,(PUBLISH,)) as (run,p,rows,key):
            kind=run['request_type']
            self.require_mode(run,p)
            need(kind==APPEND or kind in (GENERATE,REGENERATE) and p.get('motion','still')=='still')
            if kind==APPEND:
                pin={'propId':p['propId'],**p['source']}
                prior=self.writer(WRITER+'/read',pin)['manifest']
                if any(a['id']==p['motionId'] for a in prior['animations']):raise Stale()
                media={lane:self.stored(run,rows,k)['artifact']
                       for lane,k in (('detail',MOTION_DETAIL),('pixel',MOTION_PIXEL))}
                clip={'id':p['motionId'],'title':p['motionId'],'officialRequestId':run['official_request_id'],
                      'requestSha256':run['payload_sha256'],'media':media}
                prepared={**prior,'revision':prior['revision']+1,'officialRequestId':run['official_request_id'],
                          'requestSha256':run['payload_sha256'],'parentSha256':p['source']['sha256'],
                          'animations':[*prior['animations'],clip]}
                head=self.writer(WRITER+'/head',{'propId':p['propId']})['pointer']
                if _pointer(head)!=p['source']:
                    try:
                        existing=self.writer(WRITER+'/read',{'propId':p['propId'],'revision':prepared['revision'],
                                                             'sha256':digest(prepared)})['manifest']
                    except FileNotFoundError:raise Stale() from None
                    need(existing==prepared)
                return prepared
            prior=self.current_source(p,kind)
            # Read the exact two static stage artifacts; the full publish path
            # independently revalidates all lineage and raw hashes under CAS.
            media={lane:self.stored(run,rows,k)['artifact'] for lane,k in (('detail',DETAIL),('pixel',PIXEL))}
            design=self.stored(run,rows,DESIGN)['artifact']
            return {'schemaVersion':'ngs.prop-package.v1','projectId':'binari','propId':p['propId'],
                'revision':prior['revision']+1 if prior else 1,'officialRequestId':run['official_request_id'],
                'requestSha256':run['payload_sha256'],'designContextSha256':p['designContextSha256'],'title':design['title'],
                'parentSha256':p['source']['sha256'] if prior else None,'media':media,'animations':[],
                'interaction':expected_interaction(design,media)}

    def read_media(self,claim,c,m,prior):
        # Caller still holds a verified snapshot; read only exact final member
        # bytes, never a process-global cache or caller filesystem path.
        oid=claim['officialRequestId'];op=c['scope'].get('operation');groups=[]
        if op!='append-animation':groups.append((m['media'],(DETAIL,PIXEL)))
        if op=='append-animation' or c['scope'].get('motion')=='subtle':
            groups.append((m['animations'][-1]['media'],(MOTION_DETAIL,MOTION_PIXEL)))
        members={r['key']:r for r in c['members']};media={}
        for group,(dk,pk) in groups:
            for lane,k in (('detail',dk),('pixel',pk)):
                r=members[k];v=self.staging.get(oid,r['taskId'],k,r['ref'])
                need(v['artifact']==group[lane])
                if op=='append-animation':
                    existing=[prior['media'],*(x['media'] for x in prior['animations'])]
                    same=[x[lane] for x in existing if x[lane]['assetId']==v['artifact']['assetId']]
                    if not same:same=[a for y in existing for a in y.values() if a['assetId']==v['artifact']['assetId']]
                    if same:
                        need(all(a==v['artifact'] for a in same));continue
                data=self.staging.bytes(oid,r['taskId'],k,r['ref'])
                media[v['artifact']['assetId']]=base64.b64encode(data).decode()
        return media

    def authorize_read(self,operation,packet):
        # No free prop catalog through this private worker authority. Current
        # browser catalog is the ordinary authenticated replica read.
        need(operation in ('read','asset'))
        self.planner.authorized_pin(packet['pin'] if operation=='asset' else packet)

    def reference_artifact(self,pin):
        # References come only from the exact active CREATE claim, never paths,
        # arbitrary uploads or a current-version substitution.
        def confirm_head():
            if _pointer(self.writer(WRITER+'/head',{'propId':pin['propId']})['pointer']) != {
                    'revision':pin['revision'],'sha256':pin['sha256']}:
                raise ReferenceChanged()
        confirm_head()
        try:
            manifest=self.writer(WRITER+'/read',pin)['manifest']
            need(manifest['propId']==pin['propId'] and manifest['revision']==pin['revision'] and
                 digest(manifest)==pin['sha256'])
            m=manifest['media']['detail']
            need(m['mediaType']=='image/png' and 0<m['bytes']<=MAX_IMAGE_REFERENCE and m['assetId']==m['sha256']+'.png')
            asset=self.writer(WRITER+'/asset',{'pin':pin,'assetId':m['assetId']})
            raw=base64.b64decode(asset['base64'],validate=True)
            need(len(raw)==m['bytes'] and raw.startswith(b'\x89PNG\r\n\x1a\n') and
                 asset['sha256']==m['sha256']==sha(raw))
        except FileNotFoundError:raise ReferenceChanged() from None
        confirm_head()
        return {'pin':pin,'assetId':m['assetId'],'sha256':m['sha256'],'base64':asset['base64']}

    def details(self,run,p,rows,key):
        kind=run['request_type'];prior=self.current_source(p,kind)
        # The Planner authenticated the full payload hash. Runtime prep and the
        # geometry lane consume the same optional policy without altering the DAG,
        # its stored task inputs, or the immutable Filex manifest schema.
        profile={'renderProfile':p['renderProfile']} if kind==APPEND and 'renderProfile' in p else {}
        def get(k):return self.stored(run,rows,k)
        if key==DESIGN:return None
        if key==BASE:
            out={'design':get(DESIGN),'context':self.context(p,kind)}
            if kind==GENERATE and 'reference' in p:out['reference']=self.reference_artifact(p['reference'])
            return out
        if key==MOTION:
            if kind==APPEND:
                return {'package':p['source'],'media':prior['media']['detail'],'motionPrompt':p['prompt'],**profile}
            return {'source':_source(get(BASE)),'media':get(BASE)['artifact'],'motionPrompt':get(DESIGN)['artifact']['motionPrompt']}
        if key in (DETAIL,PIXEL):
            src=get(BASE if key==DETAIL else DETAIL);media=src['artifact']
            return {'source':_source(src),'canvas':canvas_of(media) if key==DETAIL else half_canvas(media),'media':media}
        src=get(MOTION if key==MOTION_DETAIL else MOTION_DETAIL)
        lane='detail' if key==MOTION_DETAIL else 'pixel'
        if kind==APPEND:
            media=prior['media'][lane];geometry={**p['source'],'media':media}
        else:
            g=get(DETAIL if key==MOTION_DETAIL else PIXEL);media=g['artifact'];geometry={**_source(g),'media':media}
        return {'source':_source(src),'canvas':canvas_of(media),
                'manifest' if key==MOTION_DETAIL else 'media':src['artifact'],'geometry':geometry,
                **(profile if key==MOTION_DETAIL else {})}

    def read_stage(self,claim,design_only):
        with self.planner.selected(claim,(DESIGN,) if design_only else tuple(k for k in STAGES if k!=DESIGN)) as (run,p,rows,key):
            self.require_mode(run,p)
            if key==DESIGN:return {'context':self.context(p,run['request_type']), 'input':strict_json(rows[key]['task_input'].encode())}
            return self.details(run,p,rows,key)

    def stage(self,claim,artifact,design_only):
        with self.planner.selected(claim,(DESIGN,) if design_only else tuple(k for k in STAGES if k!=DESIGN)) as (run,p,rows,key):
            self.require_mode(run,p)
            input=strict_json(rows[key]['task_input'].encode())
            if key==DESIGN:
                self.context(p,run['request_type']);lineage=None
            else:
                line=self.details(run,p,rows,key)
                lineage={k:line[k] for k in ('source','geometry','package') if k in line}
                if key==BASE:lineage={'design':_source(line['design'])}
                if key in (DETAIL,PIXEL,MOTION_DETAIL,MOTION_PIXEL):
                    need(type(artifact)is dict and type(artifact.get('media'))is dict and
                         all(artifact['media'].get(k)==line['canvas'][k] for k in ('width','height')))
                    times=line['manifest']['frameDurationsMs'] if key==MOTION_DETAIL else line['media'].get('frameDurationsMs',[]) if key==MOTION_PIXEL else []
                    need(artifact['media'].get('frameDurationsMs')==times)
            return self.staging.put(run['official_request_id'],claim['taskId'],key,artifact,input,lineage=lineage)

    def stage_asset(self,claim,purpose,index):
        need(purpose in ('input','geometry'))
        with self.planner.selected(claim,(MOTION,DETAIL,PIXEL,MOTION_DETAIL,MOTION_PIXEL)) as (run,p,rows,key):
            self.require_mode(run,p)
            info=self.details(run,p,rows,key);kind=run['request_type'];oid=run['official_request_id']
            if purpose=='geometry':
                need(index is None and 'geometry' in info);g=info['geometry'];m=g['media']
                if kind==APPEND:
                    file=self.writer(WRITER+'/asset',{'pin':{'propId':p['propId'],**p['source']},'assetId':m['assetId']})
                    raw=base64.b64decode(file['base64'],validate=True);need(file['sha256']==m['sha256'] and len(raw)==m['bytes'])
                    need(sha(raw)==m['sha256']);return raw,m['mediaType']
                source=DETAIL if key==MOTION_DETAIL else PIXEL
            elif key==MOTION and kind==APPEND:
                need(index is None);m=info['media']
                file=self.writer(WRITER+'/asset',{'pin':{'propId':p['propId'],**p['source']},'assetId':m['assetId']})
                raw=base64.b64decode(file['base64'],validate=True);need(file['sha256']==m['sha256'] and len(raw)==m['bytes'])
                need(sha(raw)==m['sha256']);return raw,m['mediaType']
            else:
                source={MOTION:BASE,DETAIL:BASE,PIXEL:DETAIL,MOTION_DETAIL:MOTION,MOTION_PIXEL:MOTION_DETAIL}[key]
            r=rows[source]
            data=self.staging.bytes(oid,r['task_id'],source,r['result_ref'],index)
            return data,'image/apng' if source==MOTION_DETAIL else 'image/png'

def configure_static(server,host,planner,concept,stage_root,ready_file=None):
    from .concept_filex import FilexConcept
    from .workflow_prop_package import WorkflowPropPackage,bound_prop_writer
    need(host=='127.0.0.1' and isinstance(concept,FilexConcept))
    from .filex_writer_binding import writer as connect_writer,PROP_NAMESPACE
    writer=bound_prop_writer(lambda:connect_writer('props'),namespace=PROP_NAMESPACE)
    from .prop_animation_ready import READY_FILE,renderer_ready
    authority=RealPropAuthority(PropClaimAuthority(planner),PropStageStore(stage_root),PropDesignContext(concept),writer,
                                append_readiness=lambda:renderer_ready(ready_file or READY_FILE))
    server.workflow_prop_stage=authority
    server.workflow_prop_package=WorkflowPropPackage(authority,writer)

def serve(handler):
    service=getattr(handler.server,'workflow_prop_stage',None)
    if not isinstance(service,RealPropAuthority):return handler.respond(404,{'error':'WORKFLOW_PROP_STAGE_DISABLED'})
    p=urlsplit(handler.path);design=p.path.startswith(DESIGN_ROUTE)
    if (handler.client_address[0]!='127.0.0.1' or handler.headers.get('Host')!='127.0.0.1:'+str(handler.server.server_port)
        or handler.headers.get('Origin') is not None or p.query or handler.headers.get('Transfer-Encoding') is not None):
        return handler.respond(403,{'error':'WORKFLOW_PROP_STAGE_INTERNAL_ONLY'})
    try:
        if handler.command=='GET' and p.path in (STAGE,DESIGN_ROUTE):
            if not service.readiness():return handler.respond(503,{'error':'WORKFLOW_PROP_STAGE_NOT_READY'})
            return handler.respond(200,{'enabled':True,'schemaVersion':'workflow.prop-design-write.v1' if design else 'workflow.prop-stage.v1'})
        if handler.command!='POST':return handler.respond(404,{'error':'WORKFLOW_PROP_STAGE_ROUTE'})
        n=handler.headers.get('Content-Length','');limit=MAX_BODY if p.path==STAGE else 8192
        if handler.headers.get('Content-Type')!='application/json' or not n.isdecimal() or len(n)>10 or not 0<int(n)<=limit:
            return handler.respond(400,{'error':'WORKFLOW_PROP_STAGE_BAD_BODY'})
        handler.connection.settimeout(20);raw=handler.rfile.read(int(n))
        if len(raw)!=int(n):return handler.respond(400,{'error':'WORKFLOW_PROP_STAGE_BAD_BODY'})
        packet=strict_json(raw)
        if p.path.endswith('/read'):
            need(keys(packet,('claim',)));return handler.respond(200,service.read_stage(packet['claim'],design))
        if p.path.endswith('/asset'):
            need(keys(packet,('claim','purpose','index')))
            data,mime=service.stage_asset(packet['claim'],packet['purpose'],packet['index'])
            handler.send_response(200);handler.send_header('Content-Type',mime);handler.send_header('Content-Length',str(len(data)))
            handler.send_header('Cache-Control','no-store');handler.end_headers();handler.wfile.write(data);return
        need(keys(packet,('claim','artifact')))
        return handler.respond(200,service.stage(packet['claim'],packet['artifact'],design))
    except ReferenceChanged:return handler.respond(409,{'error':'WORKFLOW_PROP_REFERENCE_CHANGED'})
    except Stale:return handler.respond(409,{'error':'WORKFLOW_PROP_STAGE_SOURCE_CHANGED'})
    except Waiting:return handler.respond(409,{'error':'WORKFLOW_PROP_STAGE_INPUT_PENDING'})
    except Denied:return handler.respond(403,{'error':'WORKFLOW_PROP_STAGE_DENIED'})
    except (FileNotFoundError,ValueError,OSError):return handler.respond(503,{'error':'WORKFLOW_PROP_STAGE_UNAVAILABLE'})
