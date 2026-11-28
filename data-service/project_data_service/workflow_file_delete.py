"""Bind the standalone file.delete task to the one pinned physical object."""
from contextlib import contextmanager
from hashlib import sha256
import re

from .filex_read import canonical,digest
from .workflow_cinematic import Denied,need,keys
from .workflow_cinematic_sequence import exact_claim,strict_json
from .single_target_delete import AuthorizedStorage,ClaimDenied,filex_execute,target_value
from .workflow_delete_claim import ClaimDenied as PlannerDenied

TYPE='file.delete'
SCHEMA='ngs.file-delete.v1'
KEY='file_delete'
PROP_ROOT='canonical-files/binari/props'
CINEMATIC_ROOT='canonical-files/binari/cinematics'
NPC_ROOT='canonical-files/binari/npcs'
BACKGROUND_ROOT='canonical-files/binari/cinematics/backgrounds'
MAP_ROOT='canonical-files/binari/cinematics/maps'
ASSEMBLY_ROOT='canonical-files/binari/cinematics/assemblies'
MAP_ID=r'request\.official\.[a-f0-9]{64}'
NPC_ID=r'(?:character|npc|entity|enemy)\.[a-z0-9._-]{2,80}'
TASK_ID=r'workflow\.task\.[a-f0-9]{64}'

def cinematic_binding(target,expected):
    if not (target_value(target) and target['root']==CINEMATIC_ROOT and keys(expected,('sourceSha256',)) and
            type(expected['sourceSha256']) is str and re.fullmatch('[a-f0-9]{64}',expected['sourceSha256'])):
        return False
    source=expected['sourceSha256'];path=target['path']
    oid=r'request\.official\.[a-f0-9]{64}'
    if target['kind']=='directory':return re.fullmatch('sequences/'+oid,path) is not None or path=='scenes/'+source
    return path in ('sources/'+source+'.json','sources/'+source+'.png') or re.fullmatch('results/'+oid+r'\.(mp4|json)',path) is not None

def active_cinematic(db,source,sequence_ids=(),*,whole_scene=False):
    # Match only the trusted structured fields, never free-form prompt text.
    render=db.execute("""SELECT 1 FROM workflow_runs r JOIN workflow_tasks t USING(official_request_id)
      WHERE r.project_id='binari' AND r.state IN ('awaiting_rs','planned') AND r.reason IS NULL
      AND r.request_type IN ('cinematic.generate','cinematic.sequence.generate')
      AND json_extract(r.payload,'$.sourceSha256')=? AND
      (t.status IN ('pending','running') OR t.status='failed' AND t.retry_count<t.retry_limit) LIMIT 1""",(source,)).fetchone()
    if render is not None:return True
    if whole_scene and db.execute("""SELECT 1 FROM workflow_runs edit JOIN workflow_tasks t USING(official_request_id)
      JOIN workflow_runs seq ON seq.official_request_id=json_extract(edit.payload,'$.sequenceId')
      WHERE edit.project_id='binari' AND edit.state IN ('awaiting_rs','planned') AND edit.reason IS NULL
      AND edit.request_type='cinematic.playback.update' AND json_extract(seq.payload,'$.sourceSha256')=?
      AND (t.status IN ('pending','running') OR t.status='failed' AND t.retry_count<t.retry_limit) LIMIT 1""",(source,)).fetchone() is not None:return True
    if not sequence_ids:return False
    return db.execute("""SELECT 1 FROM workflow_runs r JOIN workflow_tasks t USING(official_request_id)
      WHERE r.project_id='binari' AND r.state IN ('awaiting_rs','planned') AND r.reason IS NULL
      AND r.request_type='cinematic.playback.update' AND
      json_extract(r.payload,'$.sequenceId') IN (SELECT value FROM json_each(?)) AND
      (t.status IN ('pending','running') OR t.status='failed' AND t.retry_count<t.retry_limit) LIMIT 1""",
      (canonical(list(sequence_ids)).decode(),)).fetchone() is not None

def cinematic_storage_provider(planner,writer_factory,readiness):
    return AuthorizedStorage(authorize_for(planner,CINEMATIC_ROOT),lambda command:filex_execute(writer_factory,command),readiness)

def cinematic_inventory(planner,writer_factory,source):
    from .single_target_delete import CINEMATIC_INVENTORY
    need(type(source)is str and re.fullmatch('[a-f0-9]{64}',source))
    with planner.snapshot() as db:
        if active_cinematic(db,source):raise ValueError('active cinematic render')
    with writer_factory() as t:out=t.call('POST',CINEMATIC_INVENTORY,{'sourceSha256':source})
    need(keys(out,('sourceSha256','targets')) and out['sourceSha256']==source and type(out['targets']) is list and
         1<=len(out['targets'])<=512 and all(cinematic_binding(target,{'sourceSha256':source}) for target in out['targets']))
    need(len({t['path'] for t in out['targets']})==len(out['targets']))
    sequence_ids=[t['path'].split('/')[1] for t in out['targets'] if t['kind']=='directory']
    with planner.snapshot() as db:
        if active_cinematic(db,source,sequence_ids):raise ValueError('active cinematic render/playback')
    return out

def authorize_for(planner,root):
    def graph(db,oid):
        run=db.execute('SELECT * FROM workflow_runs WHERE official_request_id=?',(oid,)).fetchone()
        need(run is not None and run['project_id']=='binari' and run['request_type']==TYPE and
             run['schema_version']==SCHEMA and run['state']=='planned' and run['reason'] is None)
        raw=run['payload'].encode()
        payload=strict_json(raw)
        need(keys(payload,('schemaVersion','projectId','target','expected')) and payload['schemaVersion']==SCHEMA
             and payload['projectId']=='binari' and sha256(raw).hexdigest()==run['payload_sha256'] and canonical(payload)==raw)
        target=payload['target'];expected=payload['expected']
        need(target_value(target) and target['root']==root)
        if root==PROP_ROOT:
            need(target['kind']=='directory' and 2<=len(target['path'])<=80 and re.fullmatch(r'[a-z0-9]+(?:[-._][a-z0-9]+)*',target['path']))
            need(keys(expected,('revision','sha256')) and type(expected['revision']) is int and 1<=expected['revision']<=2147483647
                 and type(expected['sha256']) is str and re.fullmatch('[a-f0-9]{64}',expected['sha256']))
        elif root==BACKGROUND_ROOT:
            need(target['kind']=='file' and re.fullmatch(r'[a-f0-9]{64}\.(?:png|json)',target['path']) and keys(expected,('sourceRequestId',))
                 and type(expected['sourceRequestId']) is str and re.fullmatch(r'request\.official\.[a-f0-9]{64}',expected['sourceRequestId']))
        elif root in (MAP_ROOT,ASSEMBLY_ROOT):
            need(target['kind']=='directory' and re.fullmatch(MAP_ID,target['path']) and
                 keys(expected,('revision','officialRequestId')) and type(expected['revision']) is int and
                 1<=expected['revision']<2**53 and type(expected['officialRequestId']) is str and
                 re.fullmatch(MAP_ID,expected['officialRequestId']))
        elif root==CINEMATIC_ROOT:
            need(cinematic_binding(target,expected))
            sequence_ids=[target['path'].split('/')[1]] if target['kind']=='directory' else []
            whole_scene=target['path']=='scenes/'+expected['sourceSha256']
            need(not active_cinematic(db,expected['sourceSha256'],sequence_ids,whole_scene=whole_scene))
        else:
            need(target['kind']=='directory' and root==NPC_ROOT and re.fullmatch(NPC_ID,target['path']) and keys(expected,('creationTaskId',))
                 and type(expected['creationTaskId']) is str and re.fullmatch(TASK_ID,expected['creationTaskId']))
        rows=db.execute('SELECT * FROM workflow_tasks WHERE official_request_id=? LIMIT 2',(oid,)).fetchall()
        need(len(rows)==1)
        task=rows[0]
        need(task['task_id']=='workflow.task.'+digest([oid,KEY]) and task['task_key']==KEY and
             task['task_type']==TYPE and task['worker_type']=='file' and task['depends_on_task_id'] is None and
             task['task_input'].encode()==canonical({'target':target,'expected':expected}))
        return run,task,(target,expected)

    @contextmanager
    def authorize(claim):
        try:
            with exact_claim(planner,claim,graph) as (_,(target,expected)):
                yield {'operationId':claim['taskId'],'target':target,'expected':expected}
        except (Denied,PlannerDenied) as exc:raise ClaimDenied() from exc

    return authorize

def storage_provider(planner,writer_factory,readiness,root=PROP_ROOT):
    return AuthorizedStorage(authorize_for(planner,root),lambda command:filex_execute(writer_factory,command),readiness)
