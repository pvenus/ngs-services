"""Claim-bound native RGB frame staging and atomic sequence publication."""
from contextlib import contextmanager
from hashlib import sha256
import math
import os

from .filex_read import digest
from .workflow_cinematic import (
    WorkflowCinematic,READY_FILE as OLD_READY_FILE,serve as serve_cinematic,
    BAD_TEXT,JS_ENDS,CLAIM,HASH,OFFICIAL,SAFE_INT,
    need,keys,match,renderer_ready,
)
from .workflow_motion_2d import strict_json

PATH='/v1/cinematics/ownership/workflow-sequence'
WRITER='/v1/workflow/cinematics-sequence'
ROUTES=(PATH,*[PATH+'/'+suffix for suffix in ('source-status','read','asset','frame','result')])
CAPABILITY='workflow.cinematic-sequence-write.v1'
TYPE='cinematic.sequence.generate'
SCHEMA='ngs.cinematic-sequence-generate.v1'
RENDER='cinematic_sequence_render'
RENDER_TYPE=TYPE+'.render'
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


def verify_manifest(m,oid,inp):
    need(keys(m,MANIFEST) and m['schemaVersion']=='ngs.cinematic-sequence-result.v1' and
         m['projectId']=='binari' and m['officialRequestId']==oid and m['sourceSha256']==inp['sourceSha256'] and
         type(m['masterFps'])is int and m['masterFps']==24 and type(m['frameCount'])is int and m['frameCount']==COUNT and
         all(type(m[k])is int and 128<=m[k]<=512 and m[k]%32==0 for k in ('width','height')) and
         type(m['frames'])is list and len(m['frames'])==COUNT and playback(m['playback'])==inp['playback'])
    need(sum(frame_meta(row,i)['bytes'] for i,row in enumerate(m['frames']))<=MAX_TOTAL)


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
        need(path in ('','/frame','/result'))
        with self.writer_factory() as tx:
            r=tx.call('POST',WRITER+path,body,missing=missing)
        if r is None:raise FileNotFoundError('cinematic sequence not found')
        return r
    def operate(self,op,packet):
        if op=='source-status':return self.source_status(packet)
        if op=='result':return self.result(packet)
        need(op in ('read','asset','frame','publish') and keys(packet,
             ('claim','frame') if op=='frame' else ('claim','manifest') if op=='publish' else ('claim',)))
        with exact_claim(self.planner,packet['claim'],sequence_graph) as (oid,inp):
            if op=='read':
                self.source_status({'sourceSha256':inp['sourceSha256']})
                return inp
            if op=='asset':
                return self.asset_bytes(inp['sourceSha256'])
            if op=='frame':
                f=packet['frame']
                need(keys(f,('index','sha256','bytes','base64')) and type(f['base64'])is str and
                     0<len(f['base64'])<=4*((MAX_FRAME+2)//3))
                frame_meta({k:v for k,v in f.items() if k!='base64'})
                out=self.seq_call('/frame',{'officialRequestId':oid,'frame':f})
                need(out=={'ok':True,'index':f['index'],'sha256':f['sha256']})
                return out
            manifest=packet['manifest'];verify_manifest(manifest,oid,inp)
            need(self.seq_call('',{'manifest':manifest})==manifest)
            return {'ok':True,'resultRef':ref(oid)}
    def result(self,packet):
        need(keys(packet,('officialRequestId',)) and match(packet['officialRequestId'],OFFICIAL))
        oid=packet['officialRequestId']
        with self.planner.snapshot() as db:
            _,t,inp=sequence_graph(db,oid)
            need(t['status']=='completed' and t['result_ref']==ref(oid))
            m=self.seq_call('/result',packet,missing=True)
            verify_manifest(m,oid,inp)
        return {'ok':True,'resultRef':ref(oid),'manifest':m}


def serve(handler):
    return serve_cinematic(handler,attribute='workflow_cinematic_sequence',path=PATH,capability=CAPABILITY,
                           body_limits={PATH:64*1024,PATH+'/frame':6*1024*1024})
