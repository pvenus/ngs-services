"""One approved NPC planning update, not an image or initial-generation workflow."""
import hashlib
import json
import re
import sqlite3

from .filex_npc_authority import FilexCurrentNPC
from .filex_read import canonical,digest
from .pds_owned_transport import OwnedNPCTransport
from .workflow_delete_claim import CLAIM_KEYS,IDENTITY,PrivatePlannerClaim,ClaimDenied,require,matches

PATH='/v1/npcs/planning/workflow-update'
RPC='/v1/workflow/npc-planning-update'
TYPE='npc.planning.update'
SCHEMA='ngs.npc-planning-update.v1'
CAP='workflowNpcPlanningUpdateMarkdownV1'
READ_KEYS={'categoryId','role','title','source','sourceSha256','generationId'}
ROLES=('overview','portrait.art','portrait.representative','portrait.idle')
NPC=r'(?:npc|character)\.[A-Za-z0-9][A-Za-z0-9._-]{0,120}'
SHA=r'[a-f0-9]{64}'
CATEGORY=r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}'

class Invalid(ValueError):pass

def validate(ok,reason):
    if not ok:raise Invalid(reason)

def verify_payload(p):
    if not(type(p)is dict and set(p)=={'schemaVersion','projectId','npcId','role','requirements'}
           and p['schemaVersion']==SCHEMA and p['projectId']=='binari' and matches(p['npcId'],NPC) and p['role'] in ROLES):
        return False
    v=p['requirements']
    return (type(v)is str and 1<=len(v)<=4000 and v==v.strip().strip('\ufeff').strip()
            and not re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ud800-\udfff]',v))

class PlanningUpdate:
    writer_capability=CAP
    error_prefix="NPC_PLANNING_UPDATE"
    wire_schema="workflow.npc-planning-update-write.v1"

    def __init__(self,source,verifier):
        if not isinstance(source,FilexCurrentNPC) or not isinstance(verifier,PrivatePlannerClaim):
            raise ValueError('NPC_PLANNING_UPDATE_AUTHORITY')
        self.source,self.verifier=source,verifier

    def _writer(self,t):
        validate(isinstance(t,OwnedNPCTransport) and t.capabilities().get(self.writer_capability) is True,
                 self.error_prefix+'_WRITER_UNAVAILABLE')

    def capability(self):
        with self.verifier.snapshot(),self.source.store.factory() as t:self._writer(t)
        return {'enabled':True,'schemaVersion':self.wire_schema}

    def authorize(self,db,c):
        require(type(c)is dict and set(c)==CLAIM_KEYS and c['projectId']=='binari'
                and matches(c['officialRequestId'],r'request\.official\.[a-f0-9]{64}')
                and matches(c['npcId'],NPC) and matches(c['payloadSha256'],SHA)
                and type(c['claimStamp'])is int and 0<c['claimStamp']<2**53)
        oid=c['officialRequestId'];tid='workflow.task.'+digest([oid,'planning_update'])
        require(c['taskId']==tid)
        row=db.execute("""SELECT r.*,t.task_input,t.updated_at AS stamp FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
            WHERE t.task_id=? AND t.official_request_id=? AND t.task_key='planning_update' AND t.task_type='npc.planning.update'
            AND t.worker_type='document' AND t.status='running' AND t.depends_on_task_id IS NULL AND t.result_ref IS NULL
            AND r.state='planned'""",(tid,oid)).fetchone()
        require(row and row['project_id']=='binari' and row['request_type']==TYPE and row['schema_version']==SCHEMA
                and row['stamp']==c['claimStamp'] and row['registered_at']<=row['stamp'] and row['reason'] is None)
        p=json.loads(row['payload'])
        require(verify_payload(p) and p['npcId']==c['npcId'] and row['payload']==canonical(p).decode()
                and digest(p)==c['payloadSha256']==row['payload_sha256']
                and row['task_input']==canonical({'npcId':p['npcId'],'role':p['role']}).decode())
        return {**{k:c[k]for k in IDENTITY},'role':p['role']}

    def run(self,envelope,reading,started):
        validate(type(envelope)is dict and set(envelope)==({'claim'} if reading else {'claim','artifact'}),
                 'NPC_PLANNING_UPDATE_ENVELOPE')
        with self.verifier.snapshot() as db:
            binding=self.authorize(db,envelope['claim'])
            with self.source.store.factory() as t:
                self._writer(t)
                if reading:
                    r=t.call('POST',RPC+'/read',{'binding':binding})
                    if not(type(r)is dict and set(r)==READ_KEYS and r['role']==binding['role'] and
                        matches(r['categoryId'],CATEGORY) and matches(r['generationId'],r'[a-f0-9]{48}') and
                        type(r['title'])is str and r['title'].strip() and type(r['source'])is str and
                        hashlib.sha256(r['source'].encode()).hexdigest()==r['sourceSha256']):
                        raise ValueError('NPC_PLANNING_UPDATE_SOURCE_UNCONFIRMED')
                    return r
                a=envelope['artifact']
                validate(type(a)is dict and set(a)=={'schemaVersion','categoryId','role','generationId','expectedSourceSha256','content'}
                    and a['schemaVersion']=='workflow.npc-planning-update-markdown.v1' and a['role']==binding['role']
                    and matches(a['categoryId'],CATEGORY) and matches(a['generationId'],r'[a-f0-9]{48}')
                    and matches(a['expectedSourceSha256'],SHA) and type(a['content'])is str and 0<len(a['content'])<=16000
                    and bool(a['content'].strip()) and '\0' not in a['content'] and len(a['content'].encode())<=64000,
                        'NPC_PLANNING_UPDATE_ARTIFACT')
                started.append(True)
                r=t.call('POST',RPC,{'binding':binding,'artifact':a})
            if r!={'ok':True}:raise ValueError('NPC_PLANNING_UPDATE_NOT_CONFIRMED')
            return r

def serve(handler,service_type=PlanningUpdate,service_key='workflow_npc_planning_update',base=PATH):
    s=getattr(handler.server,service_key,None);err=service_type.error_prefix
    if type(s) is not service_type:return handler.respond(404,{'error':err+'_DISABLED'})
    method=handler.command;path=handler.path
    if (method not in ('GET','POST') or path not in ((base,) if method=='GET' else (base,base+'/read')) or
       handler.server.server_address[0]!='127.0.0.1' or handler.client_address[0]!='127.0.0.1' or
       handler.headers.get_all('Host')!=['127.0.0.1:'+str(handler.server.server_port)] or
       handler.headers.get('Origin') is not None or handler.headers.get('Transfer-Encoding') is not None or
       method=='POST' and handler.headers.get('Content-Type')!='application/json'):
        return handler.respond(403,{'error':err+'_INTERNAL_ONLY'})
    started=[]
    try:
        if method=='GET':return handler.respond(200,s.capability())
        handler.connection.settimeout(8)
        return handler.respond(200,s.run(handler.read_json(max_body=512*1024),path.endswith('/read'),started))
    except (ClaimDenied,Invalid) as e:
        if started:return handler.respond(409,{'error':err+'_NOT_CONFIRMED'})
        return handler.respond(403,{'error':err+'_CLAIM_DENIED' if isinstance(e,ClaimDenied) else str(e)})
    except (ValueError,KeyError,TypeError):return handler.respond(409,{'error':err+'_NOT_CONFIRMED'})
    except (OSError,sqlite3.Error):return handler.respond(503,{'error':err+'_OUTCOME_UNCONFIRMED'})
