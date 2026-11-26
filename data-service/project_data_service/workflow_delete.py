"""Exact NPC workflow delete. Default-off; no standalone caller authority."""
import copy
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import re
import sqlite3

from .workflow_delete_claim import PrivatePlannerClaim,ClaimDenied

from .filex_npc_authority import FilexCurrentNPC
from .filex_read import digest, require
from .pds_owned_transport import OwnedNPCTransport
from .motion_presets import MotionPresets

PATH='/v1/npcs/ownership/workflow-delete'
NPC_ROOT='canonical-files/binari/npcs'
IDENTITY=('officialRequestId','taskId','projectId','npcId')
KEYS=set(IDENTITY)|{'generationId','expectedSourceVersion','expectedRevision','payloadSha256'}


class WorkflowDeleteDenied(Exception):
    pass


class WorkflowDelete:
    def __init__(self,source,verifier,motion=None):
        require(isinstance(source,FilexCurrentNPC) and (callable(verifier) or isinstance(verifier,PrivatePlannerClaim)),'WORKFLOW_DELETE_AUTHORITY_REQUIRED')
        self.source,self.verifier,self.motion=source,verifier,MotionPresets() if motion is None else motion
        self.pose_recipes_ready=False
        self._folder_authorized=ContextVar('npc_storage_delete_authorized',default=None)

    def capability(self):
        with self.verifier.snapshot():
            with self.source.store.factory() as tx:
                require(isinstance(tx,OwnedNPCTransport) and tx.capabilities().get('workflowExactNpcDeleteV1') is True,
                        'WORKFLOW_DELETE_FILEX_CAPABILITY_REQUIRED')
        return {'enabled':True,'schemaVersion':'workflow.pds-delete-receipt.v1'}

    def apply(self,envelope):
        if isinstance(self.verifier,PrivatePlannerClaim):
            require(type(envelope) is dict and set(envelope)=={'claim'},'WORKFLOW_DELETE_ENVELOPE')
            with self.verifier.authorized(envelope['claim']) as (identity,selection,pose_recipe):
                return self._commit(self._resolve(identity),selection=selection,pose_recipe=pose_recipe)
        require(type(envelope) is dict and set(envelope)=={'binding','claim'},'WORKFLOW_DELETE_ENVELOPE')
        b=envelope['binding']
        require(type(b) is dict and set(b)==KEYS and type(envelope['claim']) is dict
                and b['projectId']=='binari' and type(b['expectedRevision']) is int and b['expectedRevision']>=0
                and all(type(b[k]) is str and re.fullmatch(r'[A-Za-z0-9._:-]{1,200}',b[k])
                    for k in (*IDENTITY,'generationId','expectedSourceVersion'))
                and type(b['payloadSha256']) is str and re.fullmatch(r'[a-f0-9]{64}',b['payloadSha256']),
                'WORKFLOW_DELETE_BINDING')
        # The injected authority independently resolves the exact running,
        # approved task plus source/generation. Nothing in the body grants access.
        admitted=self.verifier(copy.deepcopy(envelope['claim']))
        if type(admitted) is not dict or admitted!=b:
            raise WorkflowDeleteDenied('WORKFLOW_DELETE_CLAIM_DENIED')
        return self._commit(b)

    def _resolve(self,identity,*,creation_task=None):
        # No caller CAS. Matching history binds exact replay to the original
        # committed generation and source, never a recreated owner.
        with self.source.store.factory() as tx:
            require(isinstance(tx,OwnedNPCTransport),'WORKFLOW_DELETE_FILEX_AUTHORITY_REQUIRED')
            expected_generation=None
            if creation_task is not None:
                from .filex_cas_transport import PinnedDeleteRejected
                origin=tx.receipt(creation_task) or {}
                binding=origin.get('workflowPresetBinding',{})
                expected_generation=origin.get('generationId')
                if (origin.get('status')!='committed' or origin.get('requestId')!=creation_task
                    or type(binding) is not dict or binding.get('taskId')!=creation_task or binding.get('npcId')!=identity['npcId']
                    or binding.get('projectId')!='binari' or origin.get('workflowPresetBindingHash')!=digest(binding)
                    or type(expected_generation) is not str or not re.fullmatch(r'[a-f0-9]{48}',expected_generation)):
                    raise PinnedDeleteRejected('unknown NPC publication')
            prior=tx.receipt(identity['taskId'])
            if prior is not None:
                b=prior.get('workflowDeleteBinding',{})
                require(set(b)==KEYS and all(b[k]==v for k,v in identity.items()),'WORKFLOW_DELETE_REPLAY_BINDING')
                if creation_task is not None and b['generationId']!=expected_generation:
                    raise PinnedDeleteRejected('NPC delete receipt generation differs')
                return b
            snap,marker,_=self.source._read()
            if creation_task is not None:
                owner=snap['documents'].get(identity['npcId']+'/metadata.json',{})
                basis=owner.get('creationBasis')
                if type(basis) is not dict or basis.get('requestId')!=creation_task:
                    raise PinnedDeleteRejected('NPC selection stale')
            g=tx.get_generation(identity['npcId'])
            if creation_task is not None and (g.get('generationId')!=expected_generation or g.get('state')!='active'):
                raise PinnedDeleteRejected('NPC selection stale')
            require(g.get('npcId')==identity['npcId'] and g.get('state')=='active','WORKFLOW_DELETE_GENERATION')
            return {**identity,'generationId':g['generationId'],'expectedSourceVersion':snap['sourceVersion'],
                    'expectedRevision':marker['revision']}

    def storage_provider(self):
        from .single_target_delete import AuthorizedStorage,ClaimDenied as StorageClaimDenied
        if not isinstance(self.verifier,PrivatePlannerClaim):
            raise ValueError('WORKFLOW_DELETE_PLANNER_PIN_REQUIRED')

        def ready():
            with self.verifier.snapshot(),self.source.store.factory() as tx:
                if not isinstance(tx,OwnedNPCTransport):return False
                caps=tx.capabilities()
                return caps.get('workflowExactNpcFolderDeleteV1') is True and caps.get('workflowAtomicNpcPresetV1') is True

        @contextmanager
        def authorize(claim):
            try:
                with self.verifier.authorized(claim) as (identity,selection,pose_recipe):
                    exact={'operationId':identity['taskId'],
                           'target':{'root':NPC_ROOT,'path':identity['npcId'],'kind':'directory'}}
                    token=self._folder_authorized.set((exact,identity,selection,pose_recipe))
                    try:yield exact
                    finally:self._folder_authorized.reset(token)
            except ClaimDenied as exc:raise StorageClaimDenied() from exc

        def execute(command):
            selected=self._folder_authorized.get()
            if selected is None or command!=selected[0]:raise StorageClaimDenied()
            return self._commit(self._resolve(selected[1]),target=command['target'],selection=selected[2],pose_recipe=selected[3])

        return AuthorizedStorage(authorize,execute,ready)

    def with_standalone_storage(self, legacy):
        from .single_target_delete import AuthorizedStorage,ClaimDenied as StorageClaimDenied
        from .workflow_file_delete import authorize_for
        authorized=authorize_for(self.verifier,NPC_ROOT)
        selection=ContextVar('npc_pinned_delete_selection',default=None)

        @contextmanager
        def authorize(claim):
            if type(claim) is dict and 'npcId' in claim:
                with legacy.authorize(claim) as binding:yield binding
                return
            with authorized(claim) as command:
                identity={k:claim[k] for k in ('officialRequestId','taskId','projectId','payloadSha256')}
                token=selection.set((command,identity|{'npcId':command['target']['path']}))
                try:yield command
                finally:selection.reset(token)

        def execute(command):
            selected=selection.get()
            if selected is None:return legacy.execute(command)
            if command!=selected[0]:raise StorageClaimDenied()
            binding=self._resolve(selected[1],creation_task=command['expected']['creationTaskId'])
            return self._commit(binding,target=command['target'])

        return AuthorizedStorage(authorize,execute,legacy.ready)

    def _commit(self,b,*,target=None,selection=(),pose_recipe=False):
        bh=digest(b);rid=b['taskId'];owner=b['npcId'];revision=b['expectedRevision']
        with self.source.store.factory() as transport:
            require(isinstance(transport,OwnedNPCTransport),'WORKFLOW_DELETE_FILEX_AUTHORITY_REQUIRED')
            prior=transport.receipt(rid)
            metadata=None
            if prior is None:
                if pose_recipe:
                    require(self.pose_recipes_ready and transport.capabilities().get('workflowCombatPortraitWriteV1') is True,
                            'WORKFLOW_POSE_RECIPES_NOT_READY')
                snapshot,marker,doc=self.source._read()
                require(marker['revision']==revision and snapshot['sourceVersion']==b['expectedSourceVersion'],
                        'WORKFLOW_DELETE_SOURCE_CHANGED')
                require(sum(x['id']==owner for x in doc['characters'])==1,'WORKFLOW_DELETE_TARGET_REQUIRED')
                metadata=copy.deepcopy(snapshot['documents']['metadata.json'])
                metadata['characterOrder']=[x for x in metadata['characterOrder'] if x!=owner]
                metadata['generationEpoch']=rid
                doc={**doc,'characters':[x for x in doc['characters'] if x['id']!=owner],'generationEpoch':rid}
                metadata['_pdsCurrent']={'revision':revision+1,'savedAt':datetime.now(timezone.utc).isoformat(),
                                        'note':'Workflow exact NPC canonical deletion','operationHash':bh,
                                        'documentHash':digest(doc)}
                if selection:
                    # Fail before any NPC write when even one requested
                    # asset-set/pose has drifted. Confirmed deletion receipts
                    # remain replayable if the catalog later changes.
                    self.motion.selected_asset_sets(selection)
            receipt=transport.workflow_delete_character(b,metadata,target=target) if target is not None else transport.workflow_delete_character(b,metadata)
        require(type(receipt) is dict and receipt.get('requestId')==rid and receipt.get('inputHash')==bh
                and receipt.get('deletedCharacter')==owner and receipt.get('generationId')==b['generationId']
                and receipt.get('workflowDeleteBindingHash')==bh and receipt.get('workflowDeleteBinding')==b
                and receipt.get('status')=='committed' and receipt.get('backupVerified') is True
                and receipt.get('targetAbsent') is True and bool(receipt.get('backupId'))
                and re.fullmatch(r'[a-f0-9]{64}',str(receipt.get('backupManifestHash','')))
                and receipt.get('resultMetadata',{}).get('revision')==revision+1,
                'WORKFLOW_DELETE_RECEIPT_UNCONFIRMED')
        if target is not None:
            recovery=receipt.get('storageRecoveryId')
            require(type(recovery) is str and re.fullmatch(r'[a-f0-9]{64}',recovery),'WORKFLOW_DELETE_FOLDER_UNCONFIRMED')
            return {'ok':True,'operationId':rid,'targetAbsent':True,'recoveryId':recovery}
        return {'schemaVersion':'workflow.pds-delete-receipt.v1',
                **{k:b[k] for k in (*IDENTITY,'payloadSha256')},
                'operation':'npc.delete-folder','status':'committed','verified':True,
                'targetAbsent':True,'receiptId':rid}


def serve(handler):
    service=getattr(handler.server,'workflow_delete',None)
    if not isinstance(service,WorkflowDelete):
        return handler.respond(404,{'error':'WORKFLOW_DELETE_DISABLED'})
    if (handler.command not in ('POST','GET') or handler.path!=PATH or handler.server.server_address[0]!='127.0.0.1'
        or handler.client_address[0]!='127.0.0.1' or
        handler.headers.get_all('Host')!=['127.0.0.1:'+str(handler.server.server_port)]
        or handler.headers.get('Origin') is not None or handler.headers.get('Transfer-Encoding') is not None):
        return handler.respond(403,{'error':'WORKFLOW_DELETE_INTERNAL_ONLY'})
    try:
        if handler.command=='GET':
            if not isinstance(service.verifier,PrivatePlannerClaim):
                return handler.respond(404,{'error':'WORKFLOW_DELETE_DISABLED'})
            return handler.respond(200,service.capability())
        handler.connection.settimeout(8)
        payload=handler.read_json(max_body=65536)
        return handler.respond(200,service.apply(payload))
    except (WorkflowDeleteDenied,ClaimDenied):
        return handler.respond(403,{'error':'WORKFLOW_DELETE_CLAIM_DENIED'})
    except (ValueError,KeyError,TypeError):
        return handler.respond(409,{'error':'WORKFLOW_DELETE_NOT_CONFIRMED'})
    except (OSError,sqlite3.Error):
        return handler.respond(503,{'error':'WORKFLOW_DELETE_OUTCOME_UNCONFIRMED'})
