"""Official non-GPU authoring lane: one claim-bound metadata CAS, no frame changes."""
from hashlib import sha256
from .workflow_save_reservation import optionally_save
from .filex_read import digest
from .workflow_cinematic import WorkflowCinematic, CinematicConflict, need, keys, match, serve as serve_cinematic
from .workflow_cinematic_sequence import OFFICIAL, SAFE_INT, exact_claim, playback, strict_json, sequence_graph, ref as sequence_ref

PATH='/v1/cinematics/ownership/workflow-playback'
WRITER='/v1/workflow/cinematics-playback'
ROUTES=(PATH,PATH+'/target-status',PATH+'/result')
CAPABILITY='workflow.cinematic-playback-write.v1'
CONFLICT='WORKFLOW_CINEMATIC_PLAYBACK_CONFLICT'
TYPE='cinematic.playback.update'
SCHEMA='ngs.cinematic-playback-update.v1'
KEY='cinematic_playback_update'


def result_ref(sequence):
    return 'binari/cinematics/sequences/'+sequence+'/timeline.json'


def validate_input(v):
    need(keys(v,('sequenceId','expectedRevision','playback')) and match(v['sequenceId'],OFFICIAL) and
         type(v['expectedRevision'])is int and 0<=v['expectedRevision']<SAFE_INT)
    playback(v['playback'])
    return v


def graph(db,oid):
    need(match(oid,OFFICIAL))
    r=db.execute('SELECT * FROM workflow_runs WHERE official_request_id=?',(oid,)).fetchone()
    need(r is not None and r['request_type']==TYPE and r['schema_version']==SCHEMA and r['project_id']=='binari' and
         r['state']=='planned' and r['reason'] in (None,'RS_COMPLETED'))
    raw=r['payload'].encode('utf-8');p=strict_json(raw)
    need(keys(p,('schemaVersion','projectId','sequenceId','expectedRevision','playback')) and
         p['schemaVersion']==SCHEMA and p['projectId']=='binari' and sha256(raw).hexdigest()==r['payload_sha256'])
    inp=validate_input({k:p[k] for k in ('sequenceId','expectedRevision','playback')})
    rows=db.execute('SELECT * FROM workflow_tasks WHERE official_request_id=? LIMIT 2',(oid,)).fetchall()
    need(len(rows)==1);t=rows[0]
    candidate=validate_input(strict_json(t['task_input'].encode('utf-8')))
    need(t['task_id']=='workflow.task.'+digest([oid,KEY]) and t['task_key']==KEY and t['task_type']==TYPE and
         t['worker_type']=='file' and t['depends_on_task_id'] is None and candidate==inp)
    return r,t,inp


def state_matches(state,inp):
    return (keys(state,('schemaVersion','projectId','sequenceId','revision','playback')) and
            state['schemaVersion']=='ngs.cinematic-playback-state.v1' and state['projectId']=='binari' and
            state['sequenceId']==inp['sequenceId'] and type(state['revision'])is int and
            state['revision']==inp['expectedRevision']+1 and playback(state['playback'])==inp['playback'])


class WorkflowCinematicPlayback(WorkflowCinematic):
    def ready(self):
        with self.planner.snapshot():pass
        with self.writer_factory() as tx:
            c=tx.capabilities()
            return type(c)is dict and c.get('namespace')=='canonical-files/binari/cinematics/' and c.get('workflowCinematicPlaybackV1') is True

    def provider(self,path,body,missing=False):
        need(path in ('','/target-status','/result'))
        with self.writer_factory() as tx:
            try:v=tx.call('POST',WRITER+path,body,missing=missing)
            except ValueError as exc:
                if str(exc)=='KNOWLEDGE_SOURCE_CHANGED':raise CinematicConflict(CONFLICT) from None
                raise
        if v is None:raise FileNotFoundError('cinematic playback target not found')
        return v

    def operate(self,op,p):
        if op=='target-status':
            need(keys(p,('sequenceId','expectedRevision')) and match(p['sequenceId'],OFFICIAL) and
                 type(p['expectedRevision'])is int and 0<=p['expectedRevision']<SAFE_INT)
            v=self.provider('/target-status',p,missing=True)
            need(v=={'ok':True,'sequenceId':p['sequenceId'],'revision':p['expectedRevision']})
            return v
        if op=='result':
            need(keys(p,('officialRequestId',)) and match(p['officialRequestId'],OFFICIAL))
            oid=p['officialRequestId']
            with self.planner.snapshot() as db:
                _,t,inp=graph(db,oid)
                need(t['status']=='completed' and t['result_ref']==result_ref(inp['sequenceId']))
            value=self.provider('/result',{'officialRequestId':oid,**inp},missing=True)
            need(state_matches(value,inp))
            return {'ok':True,'resultRef':t['result_ref'],'state':value}
        need(op=='publish' and keys(p,('claim',)))
        with exact_claim(self.planner,p['claim'],graph) as (oid,inp), optionally_save(self.planner,p['claim'],op=='publish'):
            # Both the input identity and its exact published frame run are
            # authorized by the same dedicated Planner snapshot.
            with self.planner.snapshot() as db:
                _,task,_=sequence_graph(db,inp['sequenceId'])
                need(task['status']=='completed' and task['result_ref']==sequence_ref(inp['sequenceId']))
            value=self.provider('',{'officialRequestId':oid,**inp})
            need(state_matches(value,inp))
            return {'ok':True,'resultRef':result_ref(inp['sequenceId'])}


def serve(handler):
    return serve_cinematic(handler,attribute='workflow_cinematic_playback',path=PATH,capability=CAPABILITY)
