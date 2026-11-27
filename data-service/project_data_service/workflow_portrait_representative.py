"""Local-only current representative portrait, bound to the live image claim."""
import base64
import hashlib
import re
import sqlite3

from .workflow_save_reservation import save_handoff
from .filex_npc_authority import FilexCurrentNPC
from .filex_read import digest, require
from .pds_owned_transport import OwnedNPCTransport
from .workflow_delete_claim import ClaimDenied, PrivatePlannerClaim

PATH='/v1/npcs/ownership/workflow-portrait-representative'
SCHEMA='workflow.representative-portrait-write.v1'
ARTIFACT='workflow.npc-representative-portrait-artifact.v1'
CAPABILITY='workflowRepresentativePortraitCurrentFilesV1'
IDLE_PATH='/v1/npcs/ownership/workflow-portrait-idle-reference'
DIRECTION_PATH='/v1/npcs/ownership/workflow-portrait-direction'
COMBAT_PATH='/v1/npcs/ownership/workflow-portrait-combat-reference'
COMBAT_DIRECTION_PATH='/v1/npcs/ownership/workflow-portrait-combat-direction'
VARIANTS={
    'representative':(PATH,SCHEMA,ARTIFACT,CAPABILITY,'authorized_portrait',None),
    'idle':(IDLE_PATH,'workflow.idle-reference-portrait-write.v1','workflow.npc-idle-reference-portrait-artifact.v1',
            'workflowIdleReferencePortraitWriteV1','authorized_idle_portrait','representative'),
    'direction':(DIRECTION_PATH,'workflow.direction-portrait-write.v1','workflow.npc-direction-portrait-artifact.v1',
                 'workflowDirectionPortraitWriteV1','authorized_direction_portrait','idle'),
    'combat':(COMBAT_PATH,'workflow.combat-reference-portrait-write.v1','workflow.npc-combat-reference-portrait-artifact.v1',
              'workflowCombatPortraitWriteV1','authorized_combat_portrait','representative'),
    'combat_direction':(COMBAT_DIRECTION_PATH,'workflow.combat-direction-portrait-write.v1','workflow.npc-combat-direction-portrait-artifact.v1',
              'workflowCombatPortraitWriteV1','authorized_combat_direction_portrait','combat'),
}
MAX_PNG=8*1024*1024
MAX_BASE64=4*((MAX_PNG+2)//3)
TARGET={'categoryId'}


class RepresentativePortrait:
    def __init__(self,source,verifier,*,mode='representative'):
        require(isinstance(source,FilexCurrentNPC) and isinstance(verifier,PrivatePlannerClaim),
                'WORKFLOW_PORTRAIT_AUTHORITY')
        self.source,self.verifier,self.mode=source,verifier,mode
        self.path,self.schema,self.artifact,self.writes,verify,self.reference=VARIANTS[mode]
        self.authorized=getattr(verifier,verify)
        self.directional=mode in ('direction','combat_direction')
        self.pose={'representative':'representative','idle':'idle','direction':'idle','combat':'combat','combat_direction':'combat'}[mode]

    def _writer(self,tx):
        require(isinstance(tx,OwnedNPCTransport) and tx.capabilities().get(self.writes) is True,
                'WORKFLOW_PORTRAIT_WRITER_REQUIRED')

    def capability(self):
        with self.verifier.snapshot():pass
        with self.source.store.factory() as tx:
            self._writer(tx)
        return {'enabled':True,'schemaVersion':self.schema}

    def _current(self,tx,identity,character):
        owner=identity['npcId'];preset='workflow.task.'+digest([identity['officialRequestId'],'preset'])
        role='portrait.'+self.pose
        cid='category.preset.'+digest(['npc.standard','3',preset,role])[:32]
        _,_,doc=self.source._read()
        npc=next((n for n in doc['characters'] if n.get('id')==owner),None)
        require(npc is not None and npc.get('creationBasis',{}).get('requestId')==preset
                and all(npc.get(k)==character[k] for k in ('name','role','slug'))
                and npc.get('chapters')==['chapter0'+character['chapterId'][-1]],
                'WORKFLOW_PORTRAIT_CURRENT_NPC')
        slots=npc.get('portraitSets',{})
        require(isinstance(slots,dict) and (self.directional or self.pose not in slots or
                (type(slots[self.pose]) is dict and set(slots[self.pose])=={'portraitAssetId','requestId'})),
                'WORKFLOW_PORTRAIT_INCOMPATIBLE_SLOT')
        generation=tx.get_generation(owner)
        require(generation.get('npcId')==owner and generation.get('state')=='active',
                'WORKFLOW_PORTRAIT_GENERATION')
        rows=[r for r in npc.get('planningCategories',[]) if r.get('planningRole')==role]
        require(len(rows)==1 and rows[0].get('id')==cid and type(rows[0].get('content')) is str
                and bool(rows[0]['content'].strip()),'WORKFLOW_PORTRAIT_PLAN_REQUIRED')
        result={'basis':{'categoryId':cid},'character':character,'planningContent':rows[0]['content']}
        if self.reference:
            ref=slots.get(self.reference,{})
            source_key='portrait_representative' if self.reference=='representative' else 'portrait_'+self.reference+'_reference'
            source_id='workflow.task.'+digest([identity['officialRequestId'],source_key])
            asset=ref.get('portraitAssetId')
            if identity.get('referenceStage'):
                t='workflow.task.'+digest([identity['officialRequestId'],identity['referenceStage']])
                require(ref.get('pixelProvenance',{}).get('postprocess',{}).get('workflowTaskId')==t
                        and ref.get('pixelSourceAssetId')==asset,'WORKFLOW_PORTRAIT_PIXEL_REFERENCE_LINEAGE')
                asset=ref.get('pixelAssetId')
                result['basis'].update(referenceStage=identity['referenceStage'],referenceAssetId=asset)
            require(ref.get('requestId')==source_id and type(asset) is str
                    and re.fullmatch(r'[a-f0-9]{64}\.png',asset),'WORKFLOW_PORTRAIT_REFERENCE')
            raw=tx.asset_get(asset)
            require(0<len(raw)<=MAX_PNG and hashlib.sha256(raw).hexdigest()==asset[:-4],
                    'WORKFLOW_PORTRAIT_REFERENCE_BYTES')
            result['reference']={'assetId':asset,'sha256':asset[:-4],'base64':base64.b64encode(raw).decode()}
        if self.directional:result['direction']=identity['direction']
        return result

    def read(self,envelope):
        require(type(envelope) is dict and set(envelope)=={'claim'},'WORKFLOW_PORTRAIT_READ')
        with self.authorized(envelope['claim']) as (identity,character):
            with self.source.store.factory() as tx:
                self._writer(tx)
                return self._current(tx,identity,character)

    def publish(self,envelope):
        require(type(envelope) is dict and set(envelope)=={'claim','basis','artifact'},
                'WORKFLOW_PORTRAIT_PUBLISH')
        b=envelope['basis'];a=envelope['artifact']
        require(type(b) is dict and set(b) in (TARGET,TARGET|{'referenceAssetId','referenceStage'}) and type(b['categoryId']) is str
                and re.fullmatch(r'category\.preset\.[a-f0-9]{32}',b['categoryId']),'WORKFLOW_PORTRAIT_TARGET')
        require(type(a) is dict and set(a)=={'schemaVersion','taskId','projectId','npcId','sha256','base64'}
                and a['schemaVersion']==self.artifact and type(a['sha256']) is str and re.fullmatch(r'[a-f0-9]{64}',a['sha256'])
                and type(a['base64']) is str and 0<len(a['base64'])<=MAX_BASE64,'WORKFLOW_PORTRAIT_ARTIFACT')
        with self.authorized(envelope['claim']) as (identity,character):
            expected=TARGET|{'referenceAssetId','referenceStage'} if identity.get('referenceStage') else TARGET
            require(set(b)==expected and (not identity.get('referenceStage') or
                    b.get('referenceStage')==identity['referenceStage']),'WORKFLOW_PORTRAIT_REFERENCE_BASIS')
            require(all(a[k]==identity[k] for k in ('taskId','projectId','npcId')),'WORKFLOW_PORTRAIT_ID_BINDING')
            raw=base64.b64decode(a['base64'],validate=True)
            require(len(raw)<=MAX_PNG and hashlib.sha256(raw).hexdigest()==a['sha256'],'WORKFLOW_PORTRAIT_HASH')
            with save_handoff(self.verifier,envelope['claim']), self.source.store.factory() as tx:
                self._writer(tx)
                if identity.get('referenceStage'):
                    require(self._current(tx,identity,character)['basis']==b,'WORKFLOW_PORTRAIT_PIXEL_REFERENCE_CHANGED')
                result=tx.workflow_portrait_representative({**identity,**b,'sha256':a['sha256']},a['base64'],mode=self.mode)
            require(type(result) is dict and set(result)=={'ok'} and result['ok'] is True,'WORKFLOW_PORTRAIT_WRITE_NOT_CONFIRMED')
            return {'ok':True}


def serve(handler,mode='representative'):
    service=getattr(handler.server,'workflow_portrait_'+mode,None)
    if not isinstance(service,RepresentativePortrait):
        return handler.respond(404,{'error':'WORKFLOW_PORTRAIT_DISABLED'})
    method=handler.command;host='127.0.0.1:'+str(handler.server.server_port)
    if (method not in ('GET','POST') or handler.path not in ((service.path,) if method=='GET' else (service.path,service.path+'/read'))
        or handler.server.server_address[0]!='127.0.0.1'
        or handler.client_address[0]!='127.0.0.1' or handler.headers.get_all('Host')!=[host]
        or handler.headers.get('Origin') is not None or handler.headers.get('Transfer-Encoding') is not None
        or method=='POST' and handler.headers.get('Content-Type')!='application/json'):
        return handler.respond(403,{'error':'WORKFLOW_PORTRAIT_INTERNAL_ONLY'})
    payload=None
    try:
        if method=='GET':return handler.respond(200,service.capability())
        handler.connection.settimeout(8)
        reading=handler.path.endswith('/read')
        payload=handler.read_json(max_body=32768 if reading else MAX_BASE64+4096)
        return handler.respond(200,service.read(payload) if reading else service.publish(payload))
    except ClaimDenied:
        return handler.respond(403,{'error':'WORKFLOW_PORTRAIT_CLAIM_DENIED'})
    except (ValueError,KeyError,TypeError) as error:
        safe={
            'WORKFLOW_PORTRAIT_WRITER_REQUIRED','WORKFLOW_PORTRAIT_CURRENT_NPC',
            'WORKFLOW_PORTRAIT_INCOMPATIBLE_SLOT','WORKFLOW_PORTRAIT_GENERATION',
            'WORKFLOW_PORTRAIT_PLAN_REQUIRED','WORKFLOW_PORTRAIT_PIXEL_REFERENCE_LINEAGE',
            'WORKFLOW_PORTRAIT_REFERENCE','WORKFLOW_PORTRAIT_REFERENCE_BYTES',
            'WORKFLOW_PORTRAIT_PIXEL_REFERENCE_CHANGED','WORKFLOW_PORTRAIT_WRITE_NOT_CONFIRMED',
            'FILEX_CAS_HTTP_503','FILEX_CAS_HTTP_404','KNOWLEDGE_SOURCE_CHANGED',
        }
        reason=error.args[0] if len(error.args)==1 and type(error.args[0]) is str and error.args[0] in safe else 'UNCLASSIFIED'
        claim=payload.get('claim') if isinstance(payload,dict) else None
        task=claim.get('taskId') if isinstance(claim,dict) else None
        if type(task) is not str or not re.fullmatch(r'workflow\.task\.[0-9a-f]{64}',task):task='-'
        handler.log_error('workflow_portrait task=%s mode=%s phase=%s status=409 reason=%s',
                          task,mode,'read' if handler.path.endswith('/read') else 'save',reason)
        return handler.respond(409,{'error':'WORKFLOW_PORTRAIT_NOT_CONFIRMED'})
    except (OSError,sqlite3.Error):
        return handler.respond(503,{'error':'WORKFLOW_PORTRAIT_OUTCOME_UNCONFIRMED'})
