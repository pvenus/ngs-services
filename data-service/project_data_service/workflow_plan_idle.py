"""Publish only idle planning from a current, authenticated document claim."""
import re
import sqlite3

from .filex_npc_authority import FilexCurrentNPC
from .filex_read import canonical, digest, require
from .pds_owned_transport import OwnedNPCTransport
from .workflow_delete_claim import ClaimDenied, PrivatePlannerClaim
from .workflow_plan_representative import FOLDER_INPUT

PATH='/v1/npcs/ownership/workflow-plan-idle'
FOLDER_READINESS=PATH+'?inputMode='+FOLDER_INPUT
WRITE_SCHEMA='workflow.idle-plan-write.v1'
ARTIFACT='workflow.npc-idle-plan-artifact.v1'
CAPABILITY='workflowIdlePlanWriteV1'
CURRENT_CAPABILITY='workflowCurrentDocumentChainV1'
CATEGORIES={'overview':('overview','overview'),'portrait-art':('portrait-art','portrait.art'),
            'representative':('portrait.representative','portrait.representative'),'idle':('portrait.idle','portrait.idle'),'combat':('portrait.combat','portrait.combat')}
FIELDS={'overview':(), 'portrait-art':(('overview','overviewContent'),),
        'representative':(('portrait-art','portraitArtContent'),),
        'combat':(('representative','representativeContent'),('portrait-art','portraitArtContent')),
        'idle':(('representative','representativeContent'),('portrait-art','portraitArtContent'))}
BASIS={'categoryId'}


class IdlePlan:
    def __init__(self,source,verifier,*,stage='idle'):
        require(isinstance(source,FilexCurrentNPC) and isinstance(verifier,PrivatePlannerClaim),
                'WORKFLOW_IDLE_PLAN_AUTHORITY')
        self.source,self.verifier,self.stage=source,verifier,stage
        self.path='/v1/npcs/ownership/workflow-plan-'+stage
        self.readiness=self.path+'?inputMode='+FOLDER_INPUT
        self.schema='workflow.'+stage+'-plan-write.v1'
        self.artifact='workflow.npc-'+stage+'-plan-artifact.v1'

    def _writer(self,tx,chain=False):
        flag='workflowCombatPortraitWriteV1' if self.stage=='combat' else CURRENT_CAPABILITY if chain or self.stage!='idle' else CAPABILITY
        require(isinstance(tx,OwnedNPCTransport) and tx.capabilities().get(flag) is True,
                'WORKFLOW_IDLE_PLAN_WRITER_REQUIRED')

    def capability(self):
        with self.verifier.snapshot(), self.source.store.factory() as tx:
            self._writer(tx)
        return {'enabled':True,'schemaVersion':self.schema}

    def _current(self,tx,identity,character,chain):
        owner=identity['npcId']
        preset='workflow.task.'+digest([identity['officialRequestId'],'preset'])
        _,_,doc=self.source._read()
        npc=next((c for c in doc['characters'] if c['id']==owner),None)
        require(npc is not None and npc.get('creationBasis',{}).get('requestId')==preset
                and all(npc.get(k)==character[k] for k in ('name','role','slug'))
                and npc.get('chapters')==['chapter0'+character['chapterId'][-1]],
                'WORKFLOW_IDLE_PLAN_CURRENT_NPC')
        generation=tx.get_generation(owner)
        require(generation.get('npcId')==owner and generation.get('state')=='active',
                'WORKFLOW_IDLE_PLAN_GENERATION')

        def exact_category(stage,output=False):
            key,role=CATEGORIES[stage]
            cid='category.preset.'+digest(['npc.standard','3',preset,key])[:32]
            rows=npc.get('planningCategories',[])
            matches=[r for r in rows if r.get('id')==cid]
            require(len(matches)==1 and type(matches[0].get('content')) is str and
                    (matches[0].get('planningRole')==role or
                     (output and stage in ('overview','portrait-art') and 'planningRole' not in matches[0]))
                    and all(r.get('id')==cid for r in rows if r.get('planningRole')==role),
                    'WORKFLOW_CURRENT_PLAN_CATEGORY_REQUIRED')
            return matches[0]

        category=exact_category(self.stage,output=True)
        projection={'creationBasis':npc['creationBasis'],'planningPreset':npc['planningPreset'],
                    'category':{**category,'content':'','planningRole':CATEGORIES[self.stage][1]},
                    'chapters':npc['chapters'],'status':npc['status']}
        context=canonical(projection).decode()
        require(0<len(context)<=8000,'WORKFLOW_IDLE_PLAN_PRESET_LIMIT')
        result={'basis':{'categoryId':category['id']},'character':character,'presetContext':context}
        for stage,field in FIELDS[self.stage]:
            if not chain and stage=='portrait-art':continue
            upstream=exact_category(stage)
            require(bool(upstream['content'].strip()),'WORKFLOW_CURRENT_PLAN_PREREQUISITE')
            result[field]=upstream['content']
        return result

    def read_authorized(self,identity,character,chain):
        with self.source.store.factory() as tx:
            self._writer(tx,chain)
            return self._current(tx,identity,character,chain)

    def read(self,envelope):
        require(type(envelope) is dict and set(envelope)=={'claim','inputMode'}
                and envelope['inputMode']==FOLDER_INPUT,'WORKFLOW_IDLE_PLAN_READ')
        with self.verifier.authorized_document_chain(envelope['claim'],self.stage) as (identity,character,chain):
            return self.read_authorized(identity,character,chain)

    def publish_authorized(self,identity,b,a,chain):
        require(type(b) is dict and set(b)==BASIS
                and type(b['categoryId']) is str and re.fullmatch(r'category\.preset\.[a-f0-9]{32}',b['categoryId']),
                'WORKFLOW_IDLE_PLAN_BASIS')
        require(type(a) is dict and set(a)=={'schemaVersion','taskId','projectId','npcId','categoryId','content'}
                and a['schemaVersion']==self.artifact and a['categoryId']==b['categoryId']
                and type(a['content']) is str and 0<len(a['content'])<=16000 and a['content'].strip()
                and len(a['content'].encode())<=64000,'WORKFLOW_IDLE_PLAN_ARTIFACT')
        require(all(a[k]==identity[k] for k in ('taskId','projectId','npcId')),'WORKFLOW_IDLE_PLAN_ID_BINDING')
        with self.source.store.factory() as tx:
            self._writer(tx,chain)
            binding={**identity,**b}
            ack=(tx.workflow_plan_current(self.stage,binding,a['content']) if chain else
                 tx.workflow_plan_idle(binding,a['content']))
        require(type(ack) is dict and set(ack)=={'ok'} and ack['ok'] is True,
                'WORKFLOW_IDLE_PLAN_WRITE_NOT_CONFIRMED')
        return {'ok':True}

    def publish(self,envelope):
        require(type(envelope) is dict and set(envelope)=={'claim','basis','artifact'},'WORKFLOW_IDLE_PLAN_PUBLISH')
        with self.verifier.authorized_document_chain(envelope['claim'],self.stage) as (identity,_,chain):
            return self.publish_authorized(identity,envelope['basis'],envelope['artifact'],chain)


def serve(handler,stage='idle'):
    service=getattr(handler.server,'workflow_plan_'+stage.replace('-','_'),None)
    if not isinstance(service,IdlePlan):
        return handler.respond(404,{'error':'WORKFLOW_IDLE_PLAN_DISABLED'})
    method=handler.command;host='127.0.0.1:'+str(handler.server.server_port)
    if (method not in ('GET','POST')
        or handler.path not in ((service.readiness,) if method=='GET' else (service.path,service.path+'/read'))
        or handler.server.server_address[0]!='127.0.0.1'
        or handler.client_address[0]!='127.0.0.1' or handler.headers.get_all('Host')!=[host]
        or handler.headers.get('Origin') is not None or handler.headers.get('Transfer-Encoding') is not None
        or method=='POST' and handler.headers.get('Content-Type')!='application/json'):
        return handler.respond(403,{'error':'WORKFLOW_IDLE_PLAN_INTERNAL_ONLY'})
    try:
        if method=='GET':return handler.respond(200,service.capability())
        handler.connection.settimeout(8)
        payload=handler.read_json(max_body=131072)
        return handler.respond(200,service.read(payload) if handler.path.endswith('/read') else service.publish(payload))
    except ClaimDenied:
        return handler.respond(403,{'error':'WORKFLOW_IDLE_PLAN_CLAIM_DENIED'})
    except (ValueError,KeyError,TypeError):
        return handler.respond(409,{'error':'WORKFLOW_IDLE_PLAN_NOT_CONFIRMED'})
    except (OSError,sqlite3.Error):
        return handler.respond(503,{'error':'WORKFLOW_IDLE_PLAN_OUTCOME_UNCONFIRMED'})
