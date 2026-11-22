"""Private direct workflow PNG transport. Planner claims are read-only."""
import base64
import hashlib
import re
import sqlite3

from .database import DataError
from .filex_npc_authority import FilexCurrentNPC
from .filex_read import require
from .pds_owned_transport import OwnedNPCTransport
from .workflow_delete_claim import ClaimDenied,PrivatePlannerClaim
from .motion_presets import MotionPresets

PREFIX='/v1/npcs/ownership/workflow-'
CAPABILITY='workflowNativePNGMediaV1'
SCHEMAS={'animation':'workflow.animation-write.v1','frames':'workflow.frames-write.v1','portrait-pixel':'workflow.portrait-pixel-write.v1'}
MAX_BODY=90*1024*1024
ASSET=re.compile(r'[a-f0-9]{64}\.png')
MAX_STAGE_ASSET=8*1024*1024
GENERATION=re.compile(r'[a-f0-9]{48}')
HASH=re.compile(r'[a-f0-9]{64}')


class NativeMedia:
    def __init__(self,source,verifier,motion=None):
        require(isinstance(source,FilexCurrentNPC) and isinstance(verifier,PrivatePlannerClaim),'WORKFLOW_MEDIA_AUTHORITY')
        self.source,self.verifier,self.motion=source,verifier,MotionPresets() if motion is None else motion

    @staticmethod
    def _check(tx,kind,selected=False,regeneration=False):
        require(isinstance(tx,OwnedNPCTransport),'WORKFLOW_MEDIA_WRITER')
        capabilities=tx.capabilities()
        require(capabilities.get('workflowStaticPortraitPixelV1' if kind=='portrait-pixel' else CAPABILITY) is True
                and (not selected or capabilities.get('workflowSelectedMotionV1') is True)
                and (not regeneration or capabilities.get('workflowRegenerateMotionV1') is True),'WORKFLOW_MEDIA_WRITER')

    def _selected_source(self,binding):
        preset_id=binding['motion']
        pin={'presetId':preset_id,**{k:binding[k] for k in ('posePreset','assetSetSha256')}}
        preset_hash,hashes=self.motion.selected_asset_sets((pin,))[preset_id]
        return {'referenceSha256':hashes[binding['direction']],'referencePresetHash':preset_hash}

    @staticmethod
    def _regeneration_pin(value):
        require(type(value) is dict and set(value)=={'generationId','portraitAssetId','resultBasisSha256'}
                and all(type(value[key]) is str and pattern.fullmatch(value[key]) is not None
                        for key,pattern in (('generationId',GENERATION),('portraitAssetId',ASSET),('resultBasisSha256',HASH))),
                'WORKFLOW_REGENERATE_SOURCE_PIN')

    def capability(self,kind):
        with self.verifier.snapshot(),self.source.store.factory() as tx:self._check(tx,kind)
        return {'enabled':True,'schemaVersion':SCHEMAS[kind]}

    def run(self,kind,action,envelope):
        keys={'read':{'claim'},'asset':{'claim','assetId'},'publish':{'claim','artifact'}}
        require(type(envelope) is dict and set(envelope)==keys[action],'WORKFLOW_MEDIA_ENVELOPE')
        # Pin only exact current claim in a short readonly transaction. Do not
        # block planner transitions across Filex HTTP, PNG decode, or disk writes.
        def authority():
            return (self.verifier.authorized_static_pixel(envelope['claim']) if kind=='portrait-pixel' else
                    self.verifier.authorized_media(envelope['claim'],kind))
        with authority() as authorized:
            binding=dict(authorized)
        selected='assetSetSha256' in binding
        regeneration=binding.get('regeneration') is True
        reference=self._selected_source(binding) if selected and action!='asset' else None
        with self.source.store.factory() as tx:
            self._check(tx,kind,selected,regeneration)
            if action=='asset':
                aid=envelope['assetId']
                require(type(aid) is str and ASSET.fullmatch(aid),'WORKFLOW_MEDIA_ASSET')
                current=tx.workflow_media(kind,'read',binding)
                allowed=({current['source']['assetId']} if kind=='portrait-pixel' else
                         {current['portraitAssetId']} if kind=='animation' else
                         {frame['assetId'] for frame in current['sequence']['frames']})
                require(aid in allowed,'WORKFLOW_MEDIA_ASSET_SCOPE')
                if regeneration and kind=='frames':
                    result=tx.workflow_media(kind,'asset',binding,{'assetId':aid})
                    require(type(result) is dict and set(result)=={'assetId','sha256','bytes','base64'}
                            and result['assetId']==aid and result['sha256']==aid[:-4]
                            and type(result['bytes']) is int and 0<result['bytes']<=MAX_STAGE_ASSET
                            and type(result['base64']) is str and len(result['base64'])<=4*((MAX_STAGE_ASSET+2)//3),
                            'WORKFLOW_STAGE_ASSET_SCOPE')
                    data=base64.b64decode(result['base64'],validate=True)
                    require(len(data)==result['bytes'] and hashlib.sha256(data).hexdigest()==result['sha256'],
                            'WORKFLOW_STAGE_ASSET_HASH')
                    return data
                return tx.asset_get(aid)
            if action=='read':
                value=tx.workflow_media(kind,action,binding)
                if regeneration and kind=='animation':
                    pin={'generationId','portraitAssetId','resultBasisSha256'}
                    source_fields={'motion','direction','posePreset','assetSetSha256'}
                    require(type(value) is dict and set(value)==pin|source_fields
                            and all(value[k]==binding[k] for k in source_fields),'WORKFLOW_REGENERATE_SOURCE_PIN')
                    self._regeneration_pin({k:value[k] for k in pin})
                if selected and kind=='animation':return {**value,**reference}
                return value
            if regeneration and kind=='animation':
                artifact=envelope['artifact']
                require(type(artifact) is dict and set(artifact)=={'fps','width','height','frames','source'},
                        'WORKFLOW_REGENERATE_ARTIFACT')
                self._regeneration_pin(artifact['source'])
            if selected:
                # Catalog/writer I/O may have interleaved with a task change.
                # Recheck before handing off to the existing Filex owner lock,
                # without holding SQLite over the native-media transfer.
                with authority() as fresh:
                    require(fresh==binding,'WORKFLOW_MEDIA_CLAIM_CHANGED')
            ack=tx.workflow_media(kind,action,binding,envelope['artifact'])
            require(ack=={'ok':True},'WORKFLOW_MEDIA_ACK')
            return ack


def serve(handler,kind):
    service=getattr(handler.server,'workflow_native_media',None)
    if not isinstance(service,NativeMedia):
        return handler.respond(404,{'error':'WORKFLOW_MEDIA_DISABLED'})
    base=PREFIX+kind; method=handler.command
    if (method not in ('GET','POST') or handler.path not in ((base,) if method=='GET' else (base,base+'/read',base+'/asset'))
        or handler.server.server_address[0]!='127.0.0.1' or handler.client_address[0]!='127.0.0.1'
        or handler.headers.get_all('Host')!=['127.0.0.1:'+str(handler.server.server_port)]
        or handler.headers.get('Origin') is not None or handler.headers.get('Transfer-Encoding') is not None
        or method=='POST' and handler.headers.get('Content-Type')!='application/json'):
        return handler.respond(403,{'error':'WORKFLOW_MEDIA_INTERNAL_ONLY'})
    try:
        if method=='GET':return handler.respond(200,service.capability(kind))
        action='publish' if handler.path==base else handler.path.rsplit('/',1)[-1]
        handler.connection.settimeout(30)
        result=service.run(kind,action,handler.read_json(MAX_BODY if action=='publish' else 32768))
        if action=='asset':
            handler.send_response(200);handler.send_header('Content-Type','image/png')
            handler.send_header('Content-Length',str(len(result)));handler.send_header('Cache-Control','no-store')
            handler.send_header('X-Content-Type-Options','nosniff');handler.end_headers();handler.wfile.write(result)
            return
        return handler.respond(200,result)
    except ClaimDenied:
        return handler.respond(403,{'error':'WORKFLOW_MEDIA_CLAIM_DENIED'})
    except (DataError,ValueError,KeyError,TypeError):
        return handler.respond(409,{'error':'WORKFLOW_MEDIA_NOT_CONFIRMED'})
    except (OSError,sqlite3.Error):
        return handler.respond(503,{'error':'WORKFLOW_MEDIA_OUTCOME_UNCONFIRMED'})
