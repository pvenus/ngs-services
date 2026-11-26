"""Generic request boundary; configured authorities, not PDS domain decisions."""
from dataclasses import dataclass
import re
import sqlite3

from .database import DataError
from .filex_cas_transport import PinnedDeleteRejected

PATH='/v1/storage/delete-target'
NPC_STANDALONE_CAPABILITY=PATH+'/npc-standalone'
BACKGROUND_CAPABILITY=PATH+'/background'
SCHEMA='workflow.single-target-delete.v1'
SEGMENT=re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',re.ASCII)
OPERATION=re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,179}',re.ASCII)
RECOVERY=re.compile(r'[a-f0-9]{64}',re.ASCII)

class ClaimDenied(Exception):pass

def path_string(v):
    return type(v)is str and 0<len(v)<=1024 and all(SEGMENT.fullmatch(x) for x in v.split('/'))

def target_value(target):
    return (type(target)is dict and set(target)=={'root','path','kind'} and
            target['kind'] in ('file','directory') and path_string(target['root']) and path_string(target['path']))

@dataclass(frozen=True)
class AuthorizedStorage:
    # authorize(claim) is a caller-owned context manager yielding an exact
    # {operationId,target}, optionally with an operation-specific expected pin. The planner resolver belongs outside this module.
    authorize: object
    # execute(command) returns the full verified outcome. A domain can combine
    # its own bookkeeping and the generic writer move behind this port.
    execute: object
    ready: object

def filex_execute(factory,command):
    with factory() as t:return t.call('POST',PATH,command)

def filex_ready(factory):
    with factory() as t:return t.capabilities().get('singleTargetRecoverableDeleteV1') is True

class SingleTargetDelete:
    def __init__(self,roots,*,dedicated=()):
        if type(roots)is not dict or any(not path_string(k) or not isinstance(v,AuthorizedStorage)
            or not all(callable(f) for f in (v.authorize,v.execute,v.ready)) for k,v in roots.items()):
            raise ValueError('storage delete roots')
        self.roots=dict(roots)
        self.dedicated=frozenset(dedicated)
        if not self.dedicated<=self.roots.keys():raise ValueError('storage delete dedicated roots')
    def capability(self):
        roots=sorted(k for k,v in self.roots.items() if k not in self.dedicated and v.ready() is True)
        return {'enabled':bool(roots),'schemaVersion':SCHEMA,'roots':roots}
    def run(self,envelope,started):
        if type(envelope)is not dict or set(envelope)!={'claim','target'} or type(envelope['claim'])is not dict or not target_value(envelope['target']):
            raise ClaimDenied()
        target=envelope['target'];provider=self.roots.get(target['root'])
        if provider is None:raise ClaimDenied()
        if provider.ready() is not True:raise ValueError('storage delete root unavailable')
        with provider.authorize(envelope['claim']) as binding:
            if (type(binding)is not dict or set(binding) not in ({'operationId','target'},{'operationId','target','expected'}) or
                binding['target']!=target or type(binding['operationId'])is not str or not OPERATION.fullmatch(binding['operationId'])):
                raise ClaimDenied()
            started.append(True)
            receipt=provider.execute(binding)
        if (type(receipt)is not dict or set(receipt)!={'ok','operationId','targetAbsent','recoveryId'} or
            receipt['ok'] is not True or receipt['targetAbsent'] is not True or receipt['operationId']!=binding['operationId'] or
            type(receipt['recoveryId'])is not str or not RECOVERY.fullmatch(receipt['recoveryId'])):
            raise ValueError('storage delete receipt unknown')
        return receipt

def serve(handler):
    svc=getattr(handler.server,'single_target_delete',None)
    if not isinstance(svc,SingleTargetDelete):return handler.respond(404,{'error':'STORAGE_DELETE_DISABLED'})
    npc_only=handler.path==NPC_STANDALONE_CAPABILITY and handler.command=='GET'
    background_only=handler.path==BACKGROUND_CAPABILITY and handler.command=='GET'
    if (not (npc_only or background_only) and (handler.path!=PATH or handler.command not in ('GET','POST')) or
        handler.server.server_address[0]!='127.0.0.1' or handler.client_address[0]!='127.0.0.1' or
        handler.headers.get_all('Host')!=['127.0.0.1:'+str(handler.server.server_port)] or
        handler.headers.get('Origin') is not None or handler.headers.get('Transfer-Encoding') is not None or
        handler.command=='POST' and handler.headers.get('Content-Type')!='application/json'):
        return handler.respond(403,{'error':'STORAGE_DELETE_INTERNAL_ONLY'})
    started=[]
    try:
        if background_only:
            from .workflow_file_delete import BACKGROUND_ROOT
            enabled=BACKGROUND_ROOT in svc.roots and svc.roots[BACKGROUND_ROOT].ready() is True
            return handler.respond(200,{'enabled':enabled,'schemaVersion':SCHEMA,'roots':[BACKGROUND_ROOT] if enabled else []})
        if npc_only:
            from .workflow_file_delete import NPC_ROOT
            enabled=getattr(handler.server,'npc_standalone_delete',False) is True and NPC_ROOT in svc.roots and svc.roots[NPC_ROOT].ready() is True
            return handler.respond(200,{'enabled':enabled,'schemaVersion':SCHEMA,'roots':[NPC_ROOT] if enabled else []})
        if handler.command=='GET':return handler.respond(200,svc.capability())
        handler.connection.settimeout(5)
        return handler.respond(200,svc.run(handler.read_json(max_body=8192),started))
    except PinnedDeleteRejected:return handler.respond(409,{'error':'STORAGE_DELETE_STALE_TARGET'})
    except ClaimDenied:
        code,error=(409,'STORAGE_DELETE_NOT_CONFIRMED') if started else (403,'STORAGE_DELETE_CLAIM_DENIED')
        return handler.respond(code,{'error':error})
    except (ValueError,KeyError,TypeError,DataError):return handler.respond(409,{'error':'STORAGE_DELETE_NOT_CONFIRMED'})
    except (OSError,sqlite3.Error):return handler.respond(503,{'error':'STORAGE_DELETE_OUTCOME_UNCONFIRMED'})
