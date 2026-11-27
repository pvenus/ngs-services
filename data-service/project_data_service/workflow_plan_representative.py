"""Official running document task only; single current representative category."""
import copy
from datetime import datetime, timezone
import re
import sqlite3

from .filex_npc_authority import FilexCurrentNPC
from .concept_filex import FilexConcept
from .filex_read import canonical, digest, require
from .pds_owned_transport import OwnedNPCTransport
from .workflow_delete_claim import ClaimDenied, PrivatePlannerClaim
from .workflow_save_reservation import SaveReservation, publish_reserved

PATH='/v1/npcs/ownership/workflow-plan-representative'
FOLDER_INPUT='folder-snapshot'
FOLDER_READINESS=PATH+'?inputMode='+FOLDER_INPUT
WRITE_SCHEMA='workflow.representative-plan-write.v1'
ARTIFACT='workflow.npc-representative-plan-artifact.v1'
BASIS={'sourceVersion','revision','generationId','categoryId'}
CONCEPT_FIELDS={'character':('identity','persona','mutable'),
                'expression':('narrativeTone','visualStyle','visualProhibitions')}


def concept_context(reader):
    current=reader.current();doc=current['document']
    require(type(current.get('revision')) is int and doc.get('projectId') in ('ProjectBS','binari'),
            'WORKFLOW_REPRESENTATIVE_CONCEPT_SCOPE')
    sections=[]
    for sid,field_ids in CONCEPT_FIELDS.items():
        applicable=[s for s in doc['sections'] if s.get('id')==sid]
        require(len(applicable)==1,'WORKFLOW_REPRESENTATIVE_CONCEPT_SECTION')
        section=applicable[0]; selected=[]
        for fid in field_ids:
            fields=[f for f in section['fields'] if f.get('id')==fid]
            require(len(fields)==1 and bool(fields[0].get('value')),'WORKFLOW_REPRESENTATIVE_CONCEPT_FIELD')
            f=fields[0]
            selected.append({k:f[k] for k in ('id','label','value')})
        sections.append({**{k:section[k] for k in ('id','title','summary')},'fields':selected})
    text=canonical({'source':'filex','revision':current['revision'],'sections':sections}).decode()
    require(0<len(text)<=8000,'WORKFLOW_REPRESENTATIVE_CONCEPT_LIMIT')
    return text



class RepresentativePlan:
    def __init__(self,source,verifier,concept):
        require(isinstance(source,FilexCurrentNPC) and isinstance(verifier,PrivatePlannerClaim)
                and isinstance(concept,FilexConcept),
                'WORKFLOW_REPRESENTATIVE_AUTHORITY')
        self.source,self.verifier,self.concept=source,verifier,concept

    def capability(self,*,folder_snapshot=False):
        with self.verifier.snapshot():pass
        if not folder_snapshot:concept_context(self.concept)
        with self.source.store.factory() as tx:
            require(isinstance(tx,OwnedNPCTransport) and tx.capabilities().get('workflowRepresentativePlanWriteV1') is True,
                    'WORKFLOW_REPRESENTATIVE_WRITER_CAPABILITY')
        return {'enabled':True,'schemaVersion':WRITE_SCHEMA}

    def _current(self,tx,identity,character):
        owner=identity['npcId'];preset='workflow.task.'+digest([identity['officialRequestId'],'preset'])
        cid='category.preset.'+digest(['npc.standard','3',preset,'portrait.representative'])[:32]
        snap,marker,doc=self.source._read()
        npc=next((c for c in doc['characters'] if c['id']==owner),None)
        require(npc is not None and npc.get('creationBasis',{}).get('requestId')==preset
                and all(npc.get(k)==character[k] for k in ('name','role','slug'))
                and npc.get('chapters')==['chapter0'+character['chapterId'][-1]],
                'WORKFLOW_REPRESENTATIVE_CURRENT_NPC')
        g=tx.get_generation(owner)
        require(g.get('npcId')==owner and g.get('state')=='active','WORKFLOW_REPRESENTATIVE_GENERATION')
        rows=[r for r in npc.get('planningCategories',[]) if r.get('planningRole')=='portrait.representative']
        require(len(rows)==1 and rows[0].get('id')==cid and type(rows[0].get('content')) is str,
                'WORKFLOW_REPRESENTATIVE_CATEGORY_REQUIRED')
        return snap,doc,rows[0],{'sourceVersion':snap['sourceVersion'],'revision':marker['revision'],
                                 'generationId':g['generationId'],'categoryId':cid}

    def read(self,envelope):
        require(type(envelope) is dict,'WORKFLOW_REPRESENTATIVE_READ')
        folder_snapshot=set(envelope)=={'claim','inputMode'} and envelope['inputMode']==FOLDER_INPUT
        require(set(envelope)=={'claim'} or folder_snapshot,'WORKFLOW_REPRESENTATIVE_READ')
        with self.verifier.authorized_document_chain(envelope['claim'],'representative') as (identity,character,chain):
            pinned=(identity,character,chain)
        identity,character,chain=pinned
        if chain:
            require(folder_snapshot,'WORKFLOW_REPRESENTATIVE_FOLDER_REQUIRED')
            from .workflow_plan_idle import IdlePlan
            return IdlePlan(self.source,self.verifier,stage='representative').read_authorized(identity,character,True)
        with self.source.store.factory() as tx:
            require(isinstance(tx,OwnedNPCTransport),'WORKFLOW_REPRESENTATIVE_FILEX_AUTHORITY')
            _,doc,category,basis=self._current(tx,identity,character)
        npc=next(c for c in doc['characters'] if c['id']==identity['npcId'])
        # Project only fresh creation metadata; no prior principle text, siblings or assets.
        preset=canonical({'creationBasis':npc['creationBasis'],'planningPreset':npc['planningPreset'],
                          'category':{**category,'content':''},'chapters':npc['chapters'],'status':npc['status']}).decode()
        require(0<len(preset)<=8000,'WORKFLOW_REPRESENTATIVE_PRESET_LIMIT')
        result={'basis':basis,'character':character}
        if not folder_snapshot:result['conceptContext']=concept_context(self.concept)
        return {**result,'presetContext':preset}

    def publish(self,envelope):
        require(type(envelope) is dict and set(envelope)=={'claim','basis','artifact'},
                'WORKFLOW_REPRESENTATIVE_PUBLISH')
        b=envelope['basis'];a=envelope['artifact']
        def publish():
            if chain:
                from .workflow_plan_idle import IdlePlan
                return IdlePlan(self.source,self.verifier,stage='representative').publish_authorized(identity,b,a,True)
            return self._publish_legacy(identity,character,b,a)
        reservation=SaveReservation(self.verifier,envelope['claim'])
        with self.verifier.authorized_document_chain(envelope['claim'],'representative',read_save_support=True) as (identity,character,chain,enabled):
            pass
        return publish_reserved(reservation,publish) if enabled else publish()

    def _publish_legacy(self,identity,character,b,a):
        require(type(b) is dict and set(b)==BASIS and type(b['revision']) is int and b['revision']>=0
                and type(b['sourceVersion']) is str and re.fullmatch(r'[a-f0-9]{64}',b['sourceVersion'])
                and type(b['generationId']) is str and re.fullmatch(r'[a-f0-9]{48}',b['generationId'])
                and type(b['categoryId']) is str and re.fullmatch(r'category\.preset\.[a-f0-9]{32}',b['categoryId']),
                'WORKFLOW_REPRESENTATIVE_BASIS')
        require(type(a) is dict and set(a)=={'schemaVersion','taskId','projectId','npcId','categoryId','content'}
                and a['schemaVersion']==ARTIFACT and a['categoryId']==b['categoryId']
                and type(a['content']) is str and 0<len(a['content'])<=16000 and a['content'].strip()
                and len(a['content'].encode())<=64000,'WORKFLOW_REPRESENTATIVE_ARTIFACT')
        require(all(a[k]==identity[k] for k in ('taskId','projectId','npcId')),'WORKFLOW_REPRESENTATIVE_ID_BINDING')
        binding={**identity,**b,'artifactSha256':digest(a)}
        with self.source.store.factory() as tx:
            require(isinstance(tx,OwnedNPCTransport) and tx.capabilities().get('workflowRepresentativePlanWriteV1') is True,
                    'WORKFLOW_REPRESENTATIVE_WRITER_CAPABILITY')
            snap,doc,category,current=self._current(tx,identity,character)
            require(current==b,'WORKFLOW_REPRESENTATIVE_STALE_BASIS')
            category['content']=a['content']
            metadata=copy.deepcopy(snap['documents']['metadata.json'])
            metadata['_pdsCurrent']={'revision':b['revision']+1,'savedAt':datetime.now(timezone.utc).isoformat(),
                'note':'Workflow representative planning','operationHash':digest(binding),'documentHash':digest(doc)}
            ack=tx.workflow_plan_representative(binding,a['content'],metadata)
        require(type(ack) is dict and set(ack)=={'ok'} and ack['ok'] is True,
                'WORKFLOW_REPRESENTATIVE_WRITE_NOT_CONFIRMED')
        return {'ok':True}


_PLAN_CONFLICT_REASONS={
    'DOCUMENT_SAVE_IO_FAILED':'FILEX_WRITE_UNCONFIRMED',
    'WORKFLOW_REPRESENTATIVE_WRITE_NOT_CONFIRMED':'WRITER_ACK_INVALID',
    'WORKFLOW_IDLE_PLAN_WRITE_NOT_CONFIRMED':'WRITER_ACK_INVALID',
    'WORKFLOW_IDLE_PLAN_ARTIFACT':'INVALID_ARTIFACT',
    'WORKFLOW_IDLE_PLAN_ID_BINDING':'ARTIFACT_BINDING_CHANGED',
    'WORKFLOW_IDLE_PLAN_BASIS':'INVALID_ARTIFACT_BASIS',
    'WORKFLOW_CURRENT_PLAN_PREREQUISITE':'PLANNING_PREREQUISITE_MISSING',
    'KNOWLEDGE_SOURCE_CHANGED':'FILEX_WRITE_CONFLICT',
    'FILEX_CAS_HTTP_400':'FILEX_REQUEST_REJECTED',
    'FILEX_CAS_HTTP_404':'FILEX_TARGET_UNAVAILABLE',
    'FILEX_CAS_HTTP_503':'FILEX_WRITE_UNCONFIRMED',
}

def log_publication_conflict(handler, payload, stage, error):
    # The printed reason and task identifier are bounded, never raw payload or exception text.
    claim=payload.get('claim') if type(payload) is dict else None
    task=claim.get('taskId') if type(claim) is dict else None
    if type(task) is not str or not re.fullmatch(r'workflow\.task\.[a-f0-9]{64}',task):task='-'
    raw=error.args[0] if len(error.args)==1 else None
    reason=_PLAN_CONFLICT_REASONS.get(raw,'PLAN_PUBLICATION_VALIDATION_UNCLASSIFIED') if type(raw) is str else 'PLAN_PUBLICATION_VALIDATION_UNCLASSIFIED'
    safe_stage=stage if stage in ('overview','portrait-art','representative','idle','combat') else '-'
    handler.log_error('workflow_plan task=%s stage=%s status=409 reason=%s',task,safe_stage,reason)

def serve(handler):
    service=getattr(handler.server,'workflow_plan_representative',None)
    if not isinstance(service,RepresentativePlan):
        return handler.respond(404,{'error':'WORKFLOW_REPRESENTATIVE_DISABLED'})
    method=handler.command;host='127.0.0.1:'+str(handler.server.server_port)
    allowed_paths=(PATH,FOLDER_READINESS) if method=='GET' else (PATH,PATH+'/read')
    if (method not in ('GET','POST') or handler.path not in allowed_paths
        or handler.server.server_address[0]!='127.0.0.1'
        or handler.client_address[0]!='127.0.0.1' or handler.headers.get_all('Host')!=[host]
        or handler.headers.get('Origin') is not None or handler.headers.get('Transfer-Encoding') is not None
        or method=='POST' and handler.headers.get('Content-Type')!='application/json'):
        return handler.respond(403,{'error':'WORKFLOW_REPRESENTATIVE_INTERNAL_ONLY'})
    payload=None
    try:
        if method=='GET':return handler.respond(200,service.capability(folder_snapshot=handler.path==FOLDER_READINESS))
        handler.connection.settimeout(8)
        payload=handler.read_json(max_body=131072)
        data=service.read(payload) if handler.path.endswith('/read') else service.publish(payload)
        return handler.respond(200,data)
    except ClaimDenied:
        return handler.respond(403,{'error':'WORKFLOW_REPRESENTATIVE_CLAIM_DENIED'})
    except (ValueError,KeyError,TypeError) as error:
        if method=='POST' and not handler.path.endswith('/read'):
            log_publication_conflict(handler,payload,'representative',error)
        return handler.respond(409,{'error':'WORKFLOW_REPRESENTATIVE_NOT_CONFIRMED'})
    except (OSError,sqlite3.Error):
        return handler.respond(503,{'error':'WORKFLOW_REPRESENTATIVE_OUTCOME_UNCONFIRMED'})
