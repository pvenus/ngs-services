"""Claim-bound native RGB frame staging and atomic sequence publication."""
from contextlib import contextmanager, nullcontext
from hashlib import sha256
import base64
import math
import os
from urllib.parse import urlsplit

from .workflow_save_reservation import optionally_save, save_handoff
from .filex_read import digest
from .workflow_cinematic import (
    WorkflowCinematic,READY_FILE as OLD_READY_FILE,serve as serve_cinematic,
    BAD_TEXT,JS_ENDS,CLAIM,HASH,OFFICIAL,SAFE_INT,
    need,keys,match,renderer_ready,
)
from .workflow_motion_2d import strict_json

PATH='/v1/cinematics/ownership/workflow-sequence'
WRITER='/v1/workflow/cinematics-sequence'
ROUTES=(PATH,*[PATH+'/'+suffix for suffix in ('source-status','background-status','read','asset','frame','result','upscale','upscale/result','upscale-input-frame')])
CAPABILITY='workflow.cinematic-sequence-write.v1'
UPSCALE_TYPE='cinematic.sequence.upscale'
UPSCALE_SCHEMA='ngs.cinematic-sequence-upscale.v1'
UPSCALE_KEY='cinematic_sequence_upscale'
UPSCALE_FIELDS=('sequenceId','sourceSha256','expectedRevision','playback')
TYPE='cinematic.sequence.generate'
SCHEMA='ngs.cinematic-sequence-generate.v1'
RENDER='cinematic_sequence_render'
RENDER_TYPE=TYPE+'.render'
BACKGROUND_TYPE='background.animation.workflow'
BACKGROUND_SCHEMA='ngs.background-animation-workflow.v1'
BACKGROUND_STAGE_SCHEMA='ngs.background-animation-workflow.v2'
SEQUENCE_OPERATIONS={'wan':'media.animation.render','upscale':'media.frames.upscale',
                     'pixel_correct':'media.frames.pixel-correct','publish':'media.sequence.publish'}
BACKGROUND_STAGES={'cinematic':('wan','upscale','publish'),'pixel':('wan','upscale','pixel_correct','publish')}
BACKGROUND_KEYS={variant:'background_animation_'+variant for variant in ('cinematic','pixel')}
BACKGROUND_WRITER='/v1/workflow/cinematics-background'

READY_FILE=OLD_READY_FILE.with_name('cinematic-sequence-animation-ready.json')
READY_SCHEMA='workflow.cinematic-sequence-animation-renderer-ready.v1'
MAX_FRAME=4*1024*1024
MAX_TOTAL=64*1024*1024
COUNT=45
MANIFEST={'schemaVersion','projectId','officialRequestId','sourceSha256','width','height',
          'masterFps','frameCount','frames','playback'}


def ref(oid):return 'binari/cinematics/sequences/'+oid+'/manifest.json'


def playback(v):
    need(keys(v,('durationFrames','speedCurve','cameraCurve')))
    n=v['durationFrames']
    need(type(n)is int and 1<=n<=1440)
    def in_bounds(x,low,high):return type(x) in (int,float) and low<=x<=high and math.isfinite(x)
    for name,limits in (('speedCurve',{'rate':(0,4)}),('cameraCurve',{'zoom':(1,3),'x':(0,1),'y':(0,1)})):
        points=v[name]
        need(type(points)is list and 1<=len(points)<=128)
        prior=-1
        for i,pt in enumerate(points):
            need(keys(pt,('frame',*limits)) and type(pt['frame'])is int and
                 prior<pt['frame']<n and (i>0 or pt['frame']==0) and
                 all(in_bounds(pt[k],*b) for k,b in limits.items()))
            prior=pt['frame']
    return v


def frame_meta(row,index=None):
    need(keys(row,('index','sha256','bytes')) and type(row['index'])is int and 0<=row['index']<COUNT and
         (index is None or row['index']==index) and match(row['sha256'],HASH) and
         type(row['bytes'])is int and 0<row['bytes']<=MAX_FRAME)
    return row


def short_background(p):
    return p['durationFrames']==84 and len(p['speedCurve'])==1 and p['speedCurve'][0]['rate']==1/6


def upscale_proof(m,compact):
    u=m.get('upscale')
    base=('model','scale','sourceWidth','sourceHeight')+(() if 'group' in m else ('sourceSequenceId',))
    reduced=False
    if keys(u,(*base,'pixelCorrection')):
        c=u['pixelCorrection']
        reduced=compact and m['group']['variant']=='pixel'
        fields=('method','upscaledFramesSha256','downscale') if reduced else ('method','upscaledFramesSha256')
        if (m['group']['variant']!='pixel' or not keys(c,fields) or
                c['method']!='reference-palette-v1' or not match(c['upscaledFramesSha256'],HASH) or
                reduced and (type(c['downscale']) is not int or c['downscale']!=2)):
            return False
    elif not keys(u,base) or compact and m['group']['variant']=='pixel':return False
    scale=1 if reduced else 2
    return (u['model']=='seedvr2-7b-fp16' and type(u['scale'])is int and u['scale']==2 and
            all(type(u[k])is int and 128<=u[k]<=512 and u[k]%32==0 for k in ('sourceWidth','sourceHeight')) and
            (m['width'],m['height'])==(u['sourceWidth']*scale,u['sourceHeight']*scale))


def verify_manifest(m,oid,inp,group=None,original=None):
    upscale=type(m)is dict and 'upscale' in m
    compact=group is not None and short_background(inp['playback'])
    count=8 if compact else COUNT
    need((not upscale or group is not None or original is not None) and (not compact or upscale) and (original is None or upscale))
    fields=MANIFEST if group is None else MANIFEST|{'group'}
    if upscale:fields=fields|{'upscale'}
    if original is not None:
        u=m['upscale']
        need(keys(u,('model','scale','sourceWidth','sourceHeight','sourceSequenceId')) and
             u['sourceSequenceId']==inp['sequenceId']==original['officialRequestId'] and oid!=u['sourceSequenceId'] and
             (u['sourceWidth'],u['sourceHeight'])==(original['width'],original['height']))
    need(keys(m,fields) and
         (group is None or m['group']==group) and m['schemaVersion']=='ngs.cinematic-sequence-result.v1' and
         m['projectId']=='binari' and m['officialRequestId']==oid and m['sourceSha256']==inp['sourceSha256'] and
         type(m['masterFps'])is int and m['masterFps']==24 and type(m['frameCount'])is int and m['frameCount']==count and
         all(type(m[k])is int for k in ('width','height')) and
         (upscale_proof(m,compact) if upscale else all(128<=m[k]<=512 and m[k]%32==0 for k in ('width','height'))) and
         type(m['frames'])is list and len(m['frames'])==count and playback(m['playback'])==inp['playback'])
    need(sum(frame_meta(row,i)['bytes'] for i,row in enumerate(m['frames']))<=MAX_TOTAL*(2 if upscale else 1))


def upscale_graph(db,oid):
    r=db.execute('SELECT * FROM workflow_runs WHERE official_request_id=?',(oid,)).fetchone()
    need(r is not None and r['request_type']==UPSCALE_TYPE and r['schema_version']==UPSCALE_SCHEMA and
         r['project_id']=='binari' and r['state']=='planned' and r['reason'] in (None,'RS_COMPLETED'))
    raw=r['payload'].encode('utf-8');p=strict_json(raw)
    need(keys(p,('schemaVersion','projectId',*UPSCALE_FIELDS)) and p['schemaVersion']==UPSCALE_SCHEMA and
         p['projectId']=='binari' and sha256(raw).hexdigest()==r['payload_sha256'] and
         match(p['sequenceId'],OFFICIAL) and p['sequenceId']!=oid and match(p['sourceSha256'],HASH) and
         type(p['expectedRevision']) is int and 0<=p['expectedRevision']<SAFE_INT)
    playback(p['playback'])
    rows=db.execute('SELECT * FROM workflow_tasks WHERE official_request_id=? LIMIT 2',(oid,)).fetchall()
    need(len(rows)==1);t=rows[0]
    inp={k:p[k] for k in UPSCALE_FIELDS}
    need(t['task_id']=='workflow.task.'+digest([oid,UPSCALE_KEY]) and t['task_key']==UPSCALE_KEY and t['task_type']==UPSCALE_TYPE and
         t['worker_type']=='animation' and t['depends_on_task_id'] is None and strict_json(t['task_input'].encode('utf-8'))==inp)
    return r,t,inp


def sequence_graph(db,oid):
    need(match(oid,OFFICIAL))
    r=db.execute('SELECT * FROM workflow_runs WHERE official_request_id=?',(oid,)).fetchone()
    need(r is not None and r['request_type']==TYPE and r['schema_version']==SCHEMA and r['project_id']=='binari' and
         r['state']=='planned' and r['reason'] in (None,'RS_COMPLETED'))
    payload=r['payload'].encode('utf-8')
    # Bind the exact Planner bytes. Python and JS spell some IEEE doubles
    # differently (e.g. small exponent values); validate semantics separately.
    p=strict_json(payload)
    need(keys(p,('schemaVersion','projectId','sourceSha256','motionPrompt','playback')) and p['schemaVersion']==SCHEMA and
         p['projectId']=='binari' and match(p['sourceSha256'],HASH) and sha256(payload).hexdigest()==r['payload_sha256'])
    validate_input(p)
    inp={k:p[k] for k in ('sourceSha256','motionPrompt','playback')}
    rows=db.execute('SELECT * FROM workflow_tasks WHERE official_request_id=? LIMIT 2',(oid,)).fetchall()
    need(len(rows)==1);t=rows[0]
    actual=strict_json(t['task_input'].encode('utf-8'))
    need(keys(actual,('sourceSha256','motionPrompt','playback')) and validate_input(actual)==actual)
    need(t['task_id']=='workflow.task.'+digest([oid,RENDER]) and t['task_key']==RENDER and t['task_type']==RENDER_TYPE and
         t['worker_type']=='animation' and t['depends_on_task_id'] is None and actual==inp)
    return r,t,inp


def validate_input(value):
    prompt=value['motionPrompt']
    need(match(value['sourceSha256'],HASH) and type(prompt)is str and 1<=len(prompt)<=1000 and
         len(prompt.encode('utf-8'))<=2000 and prompt.strip(JS_ENDS)==prompt and BAD_TEXT.search(prompt) is None)
    playback(value['playback'])
    return value


def background_graph(db,oid,task_id):
    need(match(oid,OFFICIAL))
    r=db.execute('SELECT * FROM workflow_runs WHERE official_request_id=?',(oid,)).fetchone()
    need(r is not None and r['request_type']==BACKGROUND_TYPE and r['schema_version'] in (BACKGROUND_SCHEMA,BACKGROUND_STAGE_SCHEMA) and
         r['project_id']=='binari' and r['state']=='planned' and r['reason'] in (None,'RS_COMPLETED','RS_FAILED'))
    raw=r['payload'].encode('utf-8');p=strict_json(raw)
    need(keys(p,('schemaVersion','projectId','backgroundSha256','references','motionPrompt','playback')) and
         p['schemaVersion']==r['schema_version'] and p['projectId']=='binari' and
         sha256(raw).hexdigest()==r['payload_sha256'])
    validate_references(p['backgroundSha256'],p['references'])
    staged=p['schemaVersion']==BACKGROUND_STAGE_SCHEMA
    rows=db.execute('SELECT * FROM workflow_tasks WHERE official_request_id=? LIMIT 8',(oid,)).fetchall()
    need(len(rows)==sum(len(BACKGROUND_STAGES[v]) if staged else 1 for v in p['references']))
    by_id={t['task_id']:t for t in rows}
    chosen=None
    for variant,source in p['references'].items():
        base=BACKGROUND_KEYS[variant]
        inp=validate_input(dict(sourceSha256=source,motionPrompt=p['motionPrompt'],playback=p['playback']))
        group=dict(requestId=oid,variant=variant,backgroundSha256=p['backgroundSha256'])
        prior=None;ancestors=[]
        for stage in BACKGROUND_STAGES[variant] if staged else ('publish',):
            key=base+'_'+stage if staged else base;tid='workflow.task.'+digest([oid,key])
            t=by_id.get(tid)
            task_in={**inp,'variant':variant,'backgroundSha256':p['backgroundSha256']}
            if staged:task_in['stage']=stage
            need(t is not None and t['task_key']==key and t['task_type']==(SEQUENCE_OPERATIONS[stage] if staged else BACKGROUND_TYPE) and
                 t['worker_type']=='animation' and t['depends_on_task_id']==(prior['task_id'] if prior else None) and
                 strict_json(t['task_input'].encode('utf-8'))==task_in)
            if tid==task_id:
                if staged:
                    need(all(x['status']=='completed' and match(x['result_ref'],r'background\.transient\.[a-f0-9]{64}') for x in ancestors))
                chosen=(r,t,inp,'request.official.'+digest([oid,base]),group,p['references'],stage if staged else None)
            prior=t;ancestors.append(t)
    need(chosen is not None)
    return chosen


def validate_references(anchor,references):
    need(match(anchor,HASH) and type(references)is dict and 1<=len(references)<=2 and
         set(references)<=set(BACKGROUND_KEYS) and all(match(s,HASH) for s in references.values()) and
         len(set(references.values()))==len(references))


@contextmanager
def exact_claim(planner,claim,graph):
    need(keys(claim,CLAIM) and claim['projectId']=='binari' and match(claim['officialRequestId'],OFFICIAL) and
         match(claim['taskId'],r'workflow\.task\.[a-f0-9]{64}') and match(claim['payloadSha256'],HASH) and
         type(claim['claimStamp'])is int and 0<claim['claimStamp']<=SAFE_INT)
    oid=claim['officialRequestId']
    with planner.snapshot() as db:
        r,t,inp=graph(db,oid)
        need(r['reason'] is None and r['payload_sha256']==claim['payloadSha256'] and t['task_id']==claim['taskId'] and
             t['status']=='running' and t['result_ref'] is None and t['updated_at']==claim['claimStamp'] and
             0<=r['registered_at']<=claim['claimStamp'])
    yield oid,inp


class WorkflowCinematicSequence(WorkflowCinematic):
    def __init__(self,planner,*,writer_factory=None,readiness=None):
        super().__init__(planner,writer_factory=writer_factory,readiness=readiness or (lambda:renderer_ready(
            os.environ.get('WORKFLOW_CINEMATIC_SEQUENCE_ANIMATION_READY_FILE',READY_FILE),schema=READY_SCHEMA)))
    def capabilities(self):
        with self.writer_factory() as tx:
            caps=tx.capabilities()
            return (type(caps)is dict and caps.get('namespace')=='canonical-files/binari/cinematics/' and
                    caps.get('workflowCinematicSequenceV1') is True)
    def ready(self):
        if self.readiness() is not True:return False
        with self.planner.snapshot():pass
        return self.capabilities()
    def seq_call(self,path,body,*,missing=False):
        need(path in ('','/frame','/result','/upscale-input'))
        with self.writer_factory() as tx:
            r=tx.call('POST',WRITER+path,body,missing=missing)
        if r is None:raise FileNotFoundError('cinematic sequence not found')
        return r
    def background_ready(self):
        if not self.ready():return False
        with self.writer_factory() as tx:
            caps=tx.capabilities()
            return type(caps)is dict and caps.get('workflowBackgroundAnimationV1') is True

    def background_call(self,suffix,packet):
        with self.writer_factory() as tx:
            r=tx.call('POST',BACKGROUND_WRITER+suffix,packet,missing=True)
        if r is None:raise FileNotFoundError('background reference not found')
        return r

    def background_status(self,p):
        need(keys(p,('backgroundSha256','references')))
        anchor=p['backgroundSha256'];refs=p['references'];validate_references(anchor,refs)
        identity=self.background_call('/identity',{'sourceSha256':anchor})
        title=identity['title']
        if title.endswith(' · 픽셀 보정'):
            original_id='request.official.'+digest([identity['officialRequestId'],'background_cinematic_source'])
            if 'cinematic' in refs:
                original=self.background_call('/result',{'officialRequestId':original_id})
                need(original['sha256']==refs['cinematic'] and original['title']==title.removesuffix(' · 픽셀 보정')+' · 시네마틱 원본')
            if 'pixel' in refs:need(anchor==refs['pixel'])
        else:
            # Legacy standalone backgrounds may be animated; never accept the intermediate output.
            need(not title.endswith(' · 픽셀 생성본') and refs=={'cinematic':anchor})
        return {'ok':True}

    @contextmanager
    def render_claim(self,claim):
        need(keys(claim,CLAIM) and claim['projectId']=='binari' and match(claim['officialRequestId'],OFFICIAL) and
             match(claim['taskId'],r'workflow\.task\.[a-f0-9]{64}') and match(claim['payloadSha256'],HASH) and
             type(claim['claimStamp'])is int and 0<claim['claimStamp']<=SAFE_INT)
        oid=claim['officialRequestId']
        with self.planner.snapshot() as db:
            run=db.execute('SELECT request_type FROM workflow_runs WHERE official_request_id=?',(oid,)).fetchone()
            if run is not None and run['request_type']==BACKGROUND_TYPE:
                r,t,inp,save_id,group,refs,stage=background_graph(db,oid,claim['taskId'])
            else:
                r,t,inp=sequence_graph(db,oid);save_id=oid;group=None;refs=None;stage=None
            need(r['reason'] is None and r['payload_sha256']==claim['payloadSha256'] and t['task_id']==claim['taskId'] and
                 t['status']=='running' and t['result_ref'] is None and t['updated_at']==claim['claimStamp'] and
                 0<=r['registered_at']<=claim['claimStamp'])
        yield save_id,inp,group,refs,stage

    def upscale_ready(self):
        # Shared replicas must support derivative manifests before allowing writes.
        if os.environ.get('WORKFLOW_CINEMATIC_SEQUENCE_UPSCALE')!='1' or not self.ready():return False
        with self.writer_factory() as tx:return tx.capabilities().get('workflowCinematicUpscaleV1') is True

    def upscale_source(self,inp):
        source=self.seq_call('/upscale-input',{'sequenceId':inp['sequenceId'],'expectedRevision':inp['expectedRevision']},missing=True)
        need(keys(source,('manifest','playback')) and source['playback']==inp['playback'])
        m=source['manifest']
        verify_manifest(m,inp['sequenceId'],{'sourceSha256':inp['sourceSha256'],'playback':m['playback']})
        return m

    def upscale_operate(self,op,packet):
        need(os.environ.get('WORKFLOW_CINEMATIC_SEQUENCE_UPSCALE')=='1' and op in ('read','upscale-input-frame','frame','publish') and
             keys(packet,('claim','index') if op=='upscale-input-frame' else ('claim','frame') if op=='frame'
                  else ('claim','manifest') if op=='publish' else ('claim',)))
        c=packet['claim']
        with exact_claim(self.planner,c,upscale_graph) as (oid,inp), (save_handoff(
                self.planner,c,continuation=True,require_reservation=True) if op in ('frame','publish') else nullcontext()) as reservation:
            if op=='read':return {'input':inp,'original':self.upscale_source(inp)}
            if op=='upscale-input-frame':
                i=packet['index'];need(type(i)is int and 0<=i<COUNT)
                return self.seq_call('/upscale-input',{'sequenceId':inp['sequenceId'],'expectedRevision':inp['expectedRevision'],'index':i},missing=True)
            if op=='frame':
                f=packet['frame']
                need(keys(f,('index','sha256','bytes','base64')) and type(f['base64'])is str and
                     0<len(f['base64'])<=4*((MAX_FRAME+2)//3))
                frame_meta({k:v for k,v in f.items() if k!='base64'})
                out=self.seq_call('/frame',{'officialRequestId':oid,'frame':f,'claimStamp':c['claimStamp']})
                need(out=={'ok':True,'index':f['index'],'sha256':f['sha256']})
                return out
            original=self.upscale_source(inp)
            manifest=packet['manifest']
            verify_manifest(manifest,oid,inp,original=original)
            need(self.seq_call('',{'manifest':manifest,'claimStamp':c['claimStamp']})==manifest)
            reservation.complete(ref(oid))
            return {'ok':True,'resultRef':ref(oid)}

    def operate(self,op,packet):
        if op=='background-status':return self.background_status(packet)
        if op=='source-status':return self.source_status(packet)
        if op=='result':return self.result(packet)
        c=packet.get('claim') if isinstance(packet,dict) else None
        if type(c)is dict and match(c.get('officialRequestId'),OFFICIAL):
            with self.planner.snapshot() as db:
                r=db.execute('SELECT request_type FROM workflow_runs WHERE official_request_id=?',(c['officialRequestId'],)).fetchone()
            if r is not None and r['request_type']==UPSCALE_TYPE:return self.upscale_operate(op,packet)
        need(op in ('read','asset','frame','publish') and keys(packet,
             ('claim','frame') if op=='frame' else ('claim','manifest') if op=='publish' else ('claim',)))
        with self.render_claim(packet['claim']) as (oid,inp,group,refs,stage), optionally_save(
                self.planner,packet['claim'],op in ('frame','publish'),continuation=True):
            if stage is not None and op in ('frame','publish'):need(stage=='publish')
            if op=='read':
                if group is None:self.source_status({'sourceSha256':inp['sourceSha256']})
                else:self.background_status({'backgroundSha256':group['backgroundSha256'],
                    'references':{group['variant']:inp['sourceSha256']} if stage is not None else refs})
                return inp
            if op=='asset':
                if group is None:return self.asset_bytes(inp['sourceSha256'])
                row=self.background_call('/asset',{'sourceSha256':inp['sourceSha256']})
                need(row['sourceSha256']==inp['sourceSha256'] and type(row['base64'])is str and
                     len(row['base64'])<=4*((8*1024*1024+2)//3))
                raw=base64.b64decode(row['base64'],validate=True)
                need(raw.startswith(b'\x89PNG\r\n\x1a\n') and len(raw)<=8*1024*1024 and
                     sha256(raw).hexdigest()==inp['sourceSha256'])
                return raw
            if op=='frame':
                f=packet['frame']
                need(keys(f,('index','sha256','bytes','base64')) and type(f['base64'])is str and
                     0<len(f['base64'])<=4*((MAX_FRAME+2)//3))
                frame_meta({k:v for k,v in f.items() if k!='base64'})
                out=self.seq_call('/frame',{'officialRequestId':oid,'frame':f})
                need(out=={'ok':True,'index':f['index'],'sha256':f['sha256']})
                return out
            manifest=packet['manifest'];verify_manifest(manifest,oid,inp,group)
            if group is not None:
                need('upscale' in manifest and (group['variant']!='pixel' or 'pixelCorrection' in manifest['upscale']))
            if group is not None:self.background_status({'backgroundSha256':group['backgroundSha256'],
                'references':{group['variant']:inp['sourceSha256']}})
            need(self.seq_call('',{'manifest':manifest})==manifest)
            return {'ok':True,'resultRef':ref(oid)}
    def result(self,packet):
        if keys(packet,('officialRequestId','group')):
            group=packet['group']
            need(keys(group,('requestId','variant','backgroundSha256')) and match(group['requestId'],OFFICIAL) and
                 type(group['variant'])is str and group['variant'] in BACKGROUND_KEYS and match(group['backgroundSha256'],HASH))
            with self.planner.snapshot() as db:
                run=db.execute('SELECT schema_version FROM workflow_runs WHERE official_request_id=?',(group['requestId'],)).fetchone()
                need(run is not None)
                key=BACKGROUND_KEYS[group['variant']]+('_publish' if run['schema_version']==BACKGROUND_STAGE_SCHEMA else '')
                tid='workflow.task.'+digest([group['requestId'],key])
                _,task,inp,oid,expected,_,_=background_graph(db,group['requestId'],tid)
                need(group==expected and packet['officialRequestId']==oid and task['status']=='completed' and task['result_ref']==ref(oid))
            m=self.seq_call('/result',{'officialRequestId':oid},missing=True)
            verify_manifest(m,oid,inp,group)
            return {'ok':True,'resultRef':ref(oid),'manifest':m}
        need(keys(packet,('officialRequestId',)) and match(packet['officialRequestId'],OFFICIAL))
        oid=packet['officialRequestId']
        with self.planner.snapshot() as db:
            r=db.execute('SELECT request_type FROM workflow_runs WHERE official_request_id=?',(oid,)).fetchone()
            derived=r is not None and r['request_type']==UPSCALE_TYPE
            _,t,inp=(upscale_graph if derived else sequence_graph)(db,oid)
            need(t['status']=='completed' and t['result_ref']==ref(oid))
        m=self.seq_call('/result',packet,missing=True)
        # The original was verified at publication. Do not invalidate an already
        # published derivative if the user edits the original's playback later.
        u=m.get('upscale',{})
        original={'officialRequestId':inp['sequenceId'],'width':u.get('sourceWidth'),'height':u.get('sourceHeight')} if derived else None
        verify_manifest(m,oid,inp,original=original)
        return {'ok':True,'resultRef':ref(oid),'manifest':m}


def serve(handler):
    ready=(PATH+'/upscale','workflow.cinematic-sequence-upscale.v1','upscale_ready') if urlsplit(handler.path).path==PATH+'/upscale' else (PATH+'/background-status','workflow.background-animation-write.v1')
    return serve_cinematic(handler,attribute='workflow_cinematic_sequence',path=PATH,capability=CAPABILITY,
                           body_limits={PATH:64*1024,PATH+'/frame':6*1024*1024},
                           additional_ready=ready)
