"""Official file-worker unpublish: immutable new prop revision without touching old pixels."""
from hashlib import sha256

from .workflow_save_reservation import save_handoff
from .filex_read import digest
from .filex_writer_binding import writer as scoped_writer
from .workflow_cinematic import WorkflowCinematic, CinematicConflict, serve as serve_internal
from .workflow_cinematic_sequence import exact_claim, strict_json, OFFICIAL, SAFE_INT
from .workflow_prop_package import pin,prop_id,WRITER
from .workflow_cinematic import need,keys,match

PATH='/v1/props/ownership/workflow-animation-remove'
ROUTES=(PATH,PATH+'/result')
CAPABILITY='workflow.prop-animation-remove.v1'
CONFLICT='WORKFLOW_PROP_ANIMATION_REMOVE_CONFLICT'
TYPE='prop.animation.remove'
SCHEMA='ngs.prop-animation-remove.v1'
KEY='prop_animation_remove'
BASE=('propId','source','motionIds')

def validate_input(inp):
    need(keys(inp,BASE) and prop_id(inp['propId']) and keys(inp['source'],('revision','sha256')))
    pin({'propId':inp['propId'],**inp['source']})
    need(inp['source']['revision']<SAFE_INT-1)
    ids=inp['motionIds']
    need(type(ids)is list and 1<=len(ids)<=30 and all(prop_id(i) for i in ids) and len(set(ids))==len(ids))
    return inp

def graph(db,oid):
    need(match(oid,OFFICIAL))
    r=db.execute('SELECT * FROM workflow_runs WHERE official_request_id=?',(oid,)).fetchone()
    need(r is not None and r['request_type']==TYPE and r['schema_version']==SCHEMA and r['project_id']=='binari' and
         r['state']=='planned' and r['reason'] in (None,'RS_COMPLETED'))
    raw=r['payload'].encode('utf8');p=strict_json(raw)
    need(keys(p,('schemaVersion','projectId',*BASE)) and p['schemaVersion']==SCHEMA and p['projectId']=='binari' and
         sha256(raw).hexdigest()==r['payload_sha256'])
    inp=validate_input({k:p[k] for k in BASE})
    rows=db.execute('SELECT * FROM workflow_tasks WHERE official_request_id=? LIMIT 2',(oid,)).fetchall()
    need(len(rows)==1);t=rows[0]
    candidate=validate_input(strict_json(t['task_input'].encode('utf8')))
    need(t['task_id']=='workflow.task.'+digest([oid,KEY]) and t['task_key']==KEY and t['task_type']==TYPE and
         t['worker_type']=='file' and t['depends_on_task_id'] is None and candidate==inp)
    return r,t,inp

class WorkflowPropAnimationRemove(WorkflowCinematic):
    def __init__(self,planner,*,writer_factory=None):
        super().__init__(planner,writer_factory=writer_factory or (lambda:scoped_writer('props')))
    def ready(self):
        with self.planner.snapshot():pass
        with self.writer_factory() as tx:
            c=tx.capabilities()
            return type(c)is dict and c.get('namespace')=='canonical-files/binari/props/' and c.get('workflowPropAnimationRemoveV1') is True
    def provider(self,endpoint,p):
        need(endpoint in ('','/read'))
        with self.writer_factory() as tx:
            try:v=tx.call('POST',WRITER+endpoint,p,missing=endpoint=='/read')
            except ValueError as exc:
                if str(exc)=='KNOWLEDGE_SOURCE_CHANGED':raise CinematicConflict(CONFLICT) from None
                raise
        if v is None:raise FileNotFoundError('prop publication missing')
        return v
    def evidence(self,oid,inp,sha):
        p={'propId':inp['propId'],**inp['source']}
        original=self.provider('/read',p)['manifest']
        ids=set(inp['motionIds'])
        if not ids<={motion['id'] for motion in original['animations']}:
            raise CinematicConflict(CONFLICT)
        draft={**original,'revision':p['revision']+1,'officialRequestId':oid,'requestSha256':sha,
               'parentSha256':p['sha256'],'animations':[m for m in original['animations'] if m['id'] not in ids]}
        h=digest(draft)
        scope={'officialRequestId':oid,'projectId':'binari','propId':inp['propId'],'requestSha256':sha,
               'operation':'remove-animation','source':inp['source'],'motionIds':inp['motionIds']}
        c={'scope':scope,'members':[],'manifestSha256':h,'resultRef':'prop.package.'+h}
        return original,draft,c
    def independent(self,prior,draft,c):
        found=self.provider('/read',{'propId':draft['propId'],'revision':draft['revision'],'sha256':c['manifestSha256']})
        expected={'schemaVersion':'ngs.prop-package-publication-receipt.v1','authority':'filex','status':'committed',**c}
        need(found=={'prior':prior,'manifest':draft,'receipt':expected})
        return expected
    def operate(self,op,p):
        if op=='result':
            need(keys(p,('officialRequestId',)) and match(p['officialRequestId'],OFFICIAL))
            oid=p['officialRequestId']
            with self.planner.snapshot() as db:
                run,t,inp=graph(db,oid)
                need(t['status']=='completed')
            prior,draft,c=self.evidence(oid,inp,run['payload_sha256'])
            need(t['result_ref']==c['resultRef'])
            self.independent(prior,draft,c)
            return {'ok':True,'resultRef':c['resultRef'],'motionIds':inp['motionIds'],'propId':inp['propId'],
                    'revision':draft['revision']}
        need(op=='publish' and keys(p,('claim',)))
        with exact_claim(self.planner,p['claim'],graph) as (oid,inp), save_handoff(self.planner,p['claim']):
            prior,draft,c=self.evidence(oid,inp,p['claim']['payloadSha256'])
            receipt=self.provider('',{'candidate':c,'manifest':draft,'media':{}})
            need(self.independent(prior,draft,c)==receipt)
            return {'ok':True,'resultRef':c['resultRef']}

def serve(handler):
    return serve_internal(handler,attribute='workflow_prop_animation_remove',path=PATH,capability=CAPABILITY)
