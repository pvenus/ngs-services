"""Default-off exact workflow preset, over existing PDS→Filex authentication."""
import copy
from datetime import datetime, timezone
import re
import sqlite3

from .workflow_delete_claim import PrivatePlannerClaim,ClaimDenied

from .filex_npc_authority import FilexCurrentNPC
from .filex_npc_files import encode
from .filex_read import digest, require
from .pds_owned_transport import OwnedNPCTransport

PATH='/v1/npcs/ownership/workflow-preset'
IDENTITY=('officialRequestId','taskId','projectId','npcId','payloadSha256')
KEYS=set(IDENTITY)|{'artifactSha256','expectedSourceVersion','expectedRevision','predecessorReceiptRef'}
REGISTRY_SHA='aff780e4927c290d857cd7f36fa7f55ea0449788e8fb2b7993b1d3dd4fc0dd0c'
CATEGORIES=(('overview','인물 개요',None),('portrait-art','포트레이트용 아트',None),
            ('portrait.representative','대표 포즈 기획','portrait.representative'),
            ('portrait.idle','아이들 포즈 기획','portrait.idle'))
ARTIFACT={'schemaVersion','taskId','projectId','character','profile','presetRegistrySha256','contentGenerated','oldDataReused'}


class Denied(Exception):
    pass


def preset_character(b,a):
    from .domain_contracts.legacy_http import NPC_TEXT_FIELDS
    from .domain_contracts.profile_model import blank_profile, TRAIT_GROUPS
    require(type(a) is dict and set(a)==ARTIFACT and a['schemaVersion']=='workflow.npc-preset-artifact.v1'
            and a['projectId']==b['projectId'] and a['taskId']==b['taskId']
            and a['contentGenerated'] is False and a['oldDataReused'] is False and digest(a)==b['artifactSha256']
            and type(a['presetRegistrySha256']) is str and re.fullmatch(r'[a-f0-9]{64}',a['presetRegistrySha256']),
            'WORKFLOW_PRESET_ARTIFACT')
    c=a['character'];p=a['profile']
    require(type(c) is dict and set(c)=={'npcId','name','slug','role','actId','chapterId'}
            and c['npcId']==b['npcId'] and c['actId'] in ('act1','act01')
            and type(c['chapterId']) is str and re.fullmatch(r'(?:ch|chapter0)[1-5]',c['chapterId'])
            and all(type(c[k]) is str and 0<len(c[k].strip())<=4000 for k in ('name','role'))
            and type(c['slug']) is str and re.fullmatch(r'[a-z0-9._-]{1,128}',c['slug']),
            'WORKFLOW_PRESET_CHARACTER')
    permitted=set(NPC_TEXT_FIELDS)-{'name'}
    require(type(p) is dict and set(p)==permitted|{'traits','numericProfile','planningCategories','planningPreset'}
            and p['role']==c['role'] and all(p[k]=='' for k in permitted-{'role'})
            and p['numericProfile']==blank_profile()
            and p['traits']=={k:dict.fromkeys(fields) for k,fields in TRAIT_GROUPS.items()},
            'WORKFLOW_PRESET_PROFILE')
    categories=p['planningCategories'];preset=p['planningPreset']
    require(type(categories) is list and 0<len(categories)<=64
            and all(type(row) is dict and (set(row)=={'id','name','content'} or set(row)=={'id','name','content','planningRole'})
                    and type(row['id']) is str and re.fullmatch(r'category\.preset\.[a-f0-9]{32}',row['id'])
                    and type(row['name']) is str and 0<len(row['name'])<=200 and row['content']==''
                    and ('planningRole' not in row or type(row['planningRole']) is str and len(row['planningRole'])<=128)
                    for row in categories),
            'WORKFLOW_PRESET_CATEGORIES')
    ids=[row['id'] for row in categories]
    require(len(set(ids))==len(ids)
            and preset=={'id':'npc.standard','version':'3','visibleCategoryIds':ids},'WORKFLOW_PRESET_REGISTRY')
    return {**copy.deepcopy(p),'id':c['npcId'],'name':c['name'],'slug':c['slug'],'status':'proposal',
            'chapters':['chapter0'+c['chapterId'][-1]],'portraits':[],'activePortraitId':None,'sourceRefs':[],
            'creationBasis':{'requestId':b['taskId'],'profileState':'uninitialized'}}


class WorkflowPreset:
    def __init__(self,source,verify):
        require(isinstance(source,FilexCurrentNPC) and (callable(verify) or isinstance(verify,PrivatePlannerClaim)),'WORKFLOW_PRESET_AUTHORITY_REQUIRED')
        self.source,self.verify=source,verify

    def capability(self):
        with self.verify.snapshot():
            with self.source.store.factory() as tx:
                require(isinstance(tx,OwnedNPCTransport) and tx.capabilities().get('workflowAtomicNpcPresetV1') is True,
                        'WORKFLOW_PRESET_WRITER_CAPABILITY_REQUIRED')
        return {'enabled':True,'schemaVersion':'workflow.preset-publication-receipt.v1'}

    def apply(self,envelope):
        if isinstance(self.verify,PrivatePlannerClaim):
            require(type(envelope) is dict and set(envelope)=={'claim','artifact','predecessorReceiptRef'},
                    'WORKFLOW_PRESET_ENVELOPE')
            a=envelope['artifact']; predecessor=envelope['predecessorReceiptRef']
            require(predecessor is None or type(predecessor) is str and
                    re.fullmatch(r'workflow\.task\.[a-f0-9]{64}',predecessor),'WORKFLOW_PRESET_PREDECESSOR')
            with self.verify.authorized_preset(envelope['claim']) as (identity,character,legacy_delete):
                if legacy_delete is not None:
                    require(predecessor in (None,legacy_delete),'WORKFLOW_PRESET_PREDECESSOR')
                    predecessor=legacy_delete
                require(type(a) is dict and a.get('character')==character and a.get('presetRegistrySha256')==REGISTRY_SHA,
                        'WORKFLOW_PRESET_OFFICIAL_ARTIFACT')
                categories=[{'id':'category.preset.'+digest(['npc.standard','3',identity['taskId'],cid])[:32],
                         'name':title,'content':'',**({'planningRole':role} if role else {})}
                        for cid,title,role in CATEGORIES]
                require(type(a.get('profile')) is dict and a['profile'].get('planningCategories')==categories,
                        'WORKFLOW_PRESET_REGISTRY_CATEGORIES')
                supplied={**identity,'artifactSha256':digest(a),'predecessorReceiptRef':predecessor}
                with self.source.store.factory() as tx:
                    require(isinstance(tx,OwnedNPCTransport),'WORKFLOW_PRESET_FILEX_AUTHORITY_REQUIRED')
                    prior=tx.receipt(identity['taskId'])
                    if prior is not None:
                        b=prior.get('workflowPresetBinding',{})
                        require(set(b)==KEYS and all(b[k]==v for k,v in supplied.items()),'WORKFLOW_PRESET_REPLAY_BINDING')
                    else:
                        snap,marker,_=self.source._read()
                        b={**supplied,'expectedSourceVersion':snap['sourceVersion'],'expectedRevision':marker['revision']}
                return self._commit(b,a)
        require(type(envelope) is dict and set(envelope)=={'binding','artifact','claim'},'WORKFLOW_PRESET_ENVELOPE')
        b=envelope['binding']
        require(type(b) is dict and set(b)==KEYS and type(envelope['claim']) is dict and b['projectId']=='binari'
                and all(type(b[k]) is str and re.fullmatch(r'[A-Za-z0-9._:-]{1,200}',b[k]) for k in ('officialRequestId','taskId'))
                and all(type(b[k]) is str and re.fullmatch(r'[a-f0-9]{64}',b[k]) for k in ('artifactSha256','payloadSha256','expectedSourceVersion'))
                and type(b['npcId']) is str and re.fullmatch(r'(?:character|npc|entity|enemy)\.[a-z0-9._-]{2,80}',b['npcId'])
                and type(b['expectedRevision']) is int and b['expectedRevision']>=0
                and (b['predecessorReceiptRef'] is None or type(b['predecessorReceiptRef']) is str
                and re.fullmatch(r'[A-Za-z0-9._:-]{1,200}',b['predecessorReceiptRef'])),
                'WORKFLOW_PRESET_BINDING')
        authorized=self.verify(copy.deepcopy(envelope['claim']))
        if type(authorized) is not dict or authorized!=b:
            raise Denied('WORKFLOW_PRESET_CLAIM_DENIED')
        return self._commit(b,envelope['artifact'])

    def _commit(self,b,artifact):
        char=preset_character(b,artifact);rid=b['taskId'];bh=digest(b);files=None
        with self.source.store.factory() as tx:
            require(isinstance(tx,OwnedNPCTransport),'WORKFLOW_PRESET_FILEX_AUTHORITY_REQUIRED')
            if tx.receipt(rid) is None:
                snapshot,marker,old=self.source._read()
                require(snapshot['sourceVersion']==b['expectedSourceVersion'] and marker['revision']==b['expectedRevision'],
                        'WORKFLOW_PRESET_SOURCE_CHANGED')
                require(not any(row['id']==b['npcId'] for row in old['characters']),'WORKFLOW_PRESET_TARGET_EXISTS')
                from .domain_contracts.legacy_http import validate_npcs
                validate_npcs({**old,'characters':[char]},self.source.asset_get)
                # Encode only new character fields; never re-encode old owner files or read their assets.
                files=encode({**old,'characters':[char],'generationEpoch':rid})
                files['metadata.json']={**copy.deepcopy(snapshot['documents']['metadata.json']),
                    'characterOrder':[*snapshot['documents']['metadata.json']['characterOrder'],b['npcId']],
                    'generationEpoch':rid,'_pdsCurrent':{'revision':marker['revision']+1,
                        'savedAt':datetime.now(timezone.utc).isoformat(),'note':'Workflow NPC empty preset',
                        'operationHash':bh,'documentHash':digest({**old,'characters':[*old['characters'],char],
                                                                 'generationEpoch':rid})}}
            r=tx.workflow_preset(b,files)
        require(type(r) is dict and r.get('requestId')==rid and r.get('status')=='committed'
                and r.get('inputHash')==bh and r.get('workflowPresetBindingHash')==bh
                and r.get('workflowPresetBinding')==b and re.fullmatch(r'[a-f0-9]{48}',str(r.get('generationId')))
                and r.get('resultMetadata',{}).get('revision')==b['expectedRevision']+1,
                'WORKFLOW_PRESET_RECEIPT')
        return {'schemaVersion':'workflow.preset-publication-receipt.v1',**{k:b[k] for k in IDENTITY},
                'operation':'npc.preset','artifactSha256':b['artifactSha256'],'status':'committed',
                'verified':True,'receiptId':rid}


def serve(handler):
    service=getattr(handler.server,'workflow_preset',None)
    if not isinstance(service,WorkflowPreset):
        return handler.respond(404,{'error':'WORKFLOW_PRESET_DISABLED'})
    if (handler.command not in ('GET','POST') or handler.path!=PATH or handler.server.server_address[0]!='127.0.0.1'
        or handler.client_address[0]!='127.0.0.1' or
        handler.headers.get_all('Host')!=['127.0.0.1:'+str(handler.server.server_port)]
        or handler.headers.get('Origin') is not None or handler.headers.get('Transfer-Encoding') is not None
        or handler.command=='POST' and handler.headers.get('Content-Type')!='application/json'):
        return handler.respond(403,{'error':'WORKFLOW_PRESET_INTERNAL_ONLY'})
    try:
        if handler.command=='GET':
            if not isinstance(service.verify,PrivatePlannerClaim):
                return handler.respond(404,{'error':'WORKFLOW_PRESET_DISABLED'})
            return handler.respond(200,service.capability())
        handler.connection.settimeout(8)
        return handler.respond(200,service.apply(handler.read_json(max_body=65536)))
    except (Denied,ClaimDenied):
        return handler.respond(403,{'error':'WORKFLOW_PRESET_CLAIM_DENIED'})
    except (ValueError,KeyError,TypeError):
        return handler.respond(409,{'error':'WORKFLOW_PRESET_NOT_CONFIRMED'})
    except (OSError,sqlite3.Error):
        return handler.respond(503,{'error':'WORKFLOW_PRESET_OUTCOME_UNCONFIRMED'})
