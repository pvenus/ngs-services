"""Private direct workflow PNG transport. Planner claims are read-only."""
import re
import sqlite3

from .database import DataError
from .filex_npc_authority import FilexCurrentNPC
from .filex_read import require
from .pds_owned_transport import OwnedNPCTransport
from .workflow_delete_claim import ClaimDenied,PrivatePlannerClaim

PREFIX='/v1/npcs/ownership/workflow-'
CAPABILITY='workflowNativePNGMediaV1'
SCHEMAS={'animation':'workflow.animation-write.v1','frames':'workflow.frames-write.v1','portrait-pixel':'workflow.portrait-pixel-write.v1'}
MAX_BODY=90*1024*1024
ASSET=re.compile(r'[a-f0-9]{64}\.png')


class NativeMedia:
    def __init__(self,source,verifier):
        require(isinstance(source,FilexCurrentNPC) and isinstance(verifier,PrivatePlannerClaim),'WORKFLOW_MEDIA_AUTHORITY')
        self.source,self.verifier=source,verifier

    @staticmethod
    def _check(tx,kind):
        require(isinstance(tx,OwnedNPCTransport) and tx.capabilities().get('workflowStaticPortraitPixelV1' if kind=='portrait-pixel' else CAPABILITY) is True,'WORKFLOW_MEDIA_WRITER')

    def capability(self,kind):
        with self.verifier.snapshot(),self.source.store.factory() as tx:self._check(tx,kind)
        return {'enabled':True,'schemaVersion':SCHEMAS[kind]}

    def run(self,kind,action,envelope):
        keys={'read':{'claim'},'asset':{'claim','assetId'},'publish':{'claim','artifact'}}
        require(type(envelope) is dict and set(envelope)==keys[action],'WORKFLOW_MEDIA_ENVELOPE')
        # Pin only exact current claim in a short readonly transaction. Do not
        # block planner transitions across Filex HTTP, PNG decode, or disk writes.
        authority=(self.verifier.authorized_static_pixel(envelope['claim']) if kind=='portrait-pixel' else
                   self.verifier.authorized_media(envelope['claim'],kind))
        with authority as authorized:
            binding=dict(authorized)
        with self.source.store.factory() as tx:
            self._check(tx,kind)
            if action=='asset':
                aid=envelope['assetId']
                require(type(aid) is str and ASSET.fullmatch(aid),'WORKFLOW_MEDIA_ASSET')
                current=tx.workflow_media(kind,'read',binding)
                allowed=({current['source']['assetId']} if kind=='portrait-pixel' else
                         {current['portraitAssetId']} if kind=='animation' else
                         {frame['assetId'] for frame in current['sequence']['frames']})
                require(aid in allowed,'WORKFLOW_MEDIA_ASSET_SCOPE')
                return tx.asset_get(aid)
            if action=='read':return tx.workflow_media(kind,action,binding)
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
