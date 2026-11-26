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
NPC_ROOT='canonical-files/binari/npcs'
NPC_ID=r'(?:character|npc|entity|enemy)\.[a-z0-9._-]{2,80}'
TASK_ID=r'workflow\.task\.[a-f0-9]{64}'

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
        need(target_value(target) and target['root']==root and target['kind']=='directory')
        if root==PROP_ROOT:
            need(2<=len(target['path'])<=80 and re.fullmatch(r'[a-z0-9]+(?:[-._][a-z0-9]+)*',target['path']))
            need(keys(expected,('revision','sha256')) and type(expected['revision']) is int and 1<=expected['revision']<=2147483647
                 and type(expected['sha256']) is str and re.fullmatch('[a-f0-9]{64}',expected['sha256']))
        else:
            need(root==NPC_ROOT and re.fullmatch(NPC_ID,target['path']) and keys(expected,('creationTaskId',))
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

def storage_provider(planner,writer_factory,readiness):
    return AuthorizedStorage(authorize_for(planner,PROP_ROOT),lambda command:filex_execute(writer_factory,command),readiness)
