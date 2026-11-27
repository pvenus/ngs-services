"""Authorize just one new standalone NPC planning Markdown document."""
import json
import re

from .workflow_save_reservation import optionally_save
from .filex_read import canonical,digest
from .workflow_concept_category import required_headings
from .workflow_delete_claim import CLAIM_KEYS,IDENTITY,require,matches
from .workflow_npc_planning_update import PlanningUpdate,validate,serve as serve_planning,NPC,SHA

PATH='/v1/npcs/planning/workflow-add'
RPC='/v1/workflow/npc-planning-add'
TYPE='npc.planning.add'
SCHEMA='ngs.npc-planning-add.v1'
CAP='workflowNpcPlanningAddMarkdownV1'
CATEGORY=r'category\.add\.[a-f0-9]{32}'
ARTIFACT='workflow.npc-planning-add-markdown.v1'

def text(v,lo,hi):
    return (type(v)is str and lo<=len(v)<=hi and v==v.strip().strip('\ufeff').strip()
            and not re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]',v))

def verify_payload(p):
    return (type(p)is dict and set(p)=={'schemaVersion','projectId','npcId','categoryId','title','outline','requirements','requiredSections'}
            and p['schemaVersion']==SCHEMA and p['projectId']=='binari' and matches(p['npcId'],NPC)
            and matches(p['categoryId'],CATEGORY) and text(p['title'],1,120) and not re.search(r'[\t\r\n\u2028\u2029]',p['title'])
            and text(p['outline'],1,4000) and text(p['requirements'],0,4000) and required_headings(p['requiredSections']))

class PlanningAdd(PlanningUpdate):
    writer_capability=CAP
    error_prefix='NPC_PLANNING_ADD'
    wire_schema='workflow.npc-planning-add-write.v1'

    def authorize(self,db,c):
        require(type(c)is dict and set(c)==CLAIM_KEYS and c['projectId']=='binari'
                and matches(c['officialRequestId'],r'request\.official\.[a-f0-9]{64}')
                and matches(c['npcId'],NPC) and matches(c['payloadSha256'],SHA)
                and type(c['claimStamp'])is int and 0<c['claimStamp']<2**53)
        oid=c['officialRequestId'];tid='workflow.task.'+digest([oid,'planning_add'])
        require(c['taskId']==tid)
        row=db.execute("""SELECT r.*,t.task_input,t.updated_at AS stamp FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
            WHERE t.task_id=? AND t.official_request_id=? AND t.task_key='planning_add' AND t.task_type='npc.planning.add'
            AND t.worker_type='document' AND t.status='running' AND t.depends_on_task_id IS NULL AND t.result_ref IS NULL
            AND r.state='planned'""",(tid,oid)).fetchone()
        require(row and row['project_id']=='binari' and row['request_type']==TYPE and row['schema_version']==SCHEMA
                and row['stamp']==c['claimStamp'] and row['registered_at']<=row['stamp'] and row['reason'] is None)
        p=json.loads(row['payload'])
        require(verify_payload(p) and p['npcId']==c['npcId'] and row['payload']==canonical(p).decode()
                and digest(p)==c['payloadSha256']==row['payload_sha256']
                and row['task_input']==canonical({k:p[k] for k in ('npcId','categoryId')}).decode())
        return {**{k:c[k] for k in IDENTITY},'categoryId':p['categoryId'],'title':p['title']}

    def run(self,envelope,reading,started):
        validate(type(envelope)is dict and set(envelope)==({'claim'} if reading else {'claim','artifact'}),
                 'NPC_PLANNING_ADD_ENVELOPE')
        with self.verifier.snapshot() as db:
            binding=self.authorize(db,envelope['claim'])
        with optionally_save(self.verifier,envelope['claim'],not reading):
            with self.source.store.factory() as t:
                self._writer(t)
                if reading:
                    r=t.call('POST',RPC+'/read',{'binding':binding})
                    if not(type(r)is dict and set(r)=={'categoryId','title','generationId'}
                        and all(r[k]==binding[k]for k in ('categoryId','title'))
                        and matches(r['generationId'],r'[a-f0-9]{48}')):
                        raise ValueError('NPC_PLANNING_ADD_SCOPE_UNCONFIRMED')
                    return r
                a=envelope['artifact']
                validate(type(a)is dict and set(a)=={'schemaVersion','categoryId','title','generationId','content'}
                    and a['schemaVersion']==ARTIFACT and all(a[k]==binding[k]for k in ('categoryId','title'))
                    and matches(a['generationId'],r'[a-f0-9]{48}') and type(a['content'])is str and 0<len(a['content'])<=16000
                    and bool(a['content'].strip()) and '\0' not in a['content'] and len(a['content'].encode())<=64000,
                        'NPC_PLANNING_ADD_ARTIFACT')
                started.append(True)
                r=t.call('POST',RPC,{'binding':binding,'artifact':a})
            if r!={'ok':True}:raise ValueError('NPC_PLANNING_ADD_NOT_CONFIRMED')
            return r

def serve(handler):
    return serve_planning(handler,PlanningAdd,'workflow_npc_planning_add',PATH)
