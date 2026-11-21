"""Request-local NPC authority guard; no intent/prompt fields or local asset paths."""
import base64,json,re
from contextvars import ContextVar
from contextlib import contextmanager
from .filex_read import require
GUARD=ContextVar('npc_ownership_guard',default=None)

def parse_headers(headers):
 raw=headers.get('X-NPC-Ownership-Guard');stage=headers.get('X-NPC-Stage-Request-Id')
 if raw is None:
  require(stage is None,'NPC_GUARD_HEADER_REQUIRED');return None
 require(len(raw)<=4096,'NPC_GUARD_HEADER_TOO_LARGE')
 value=json.loads(base64.b64decode(raw,validate=True))
 require(isinstance(value,dict),'NPC_GUARD_SCHEMA')
 lifecycle=value.get('operation') in ('create','restore')
 required={'npcId','generationId','requestId','operation','lifecycleToken','expectedSourceVersion'} if lifecycle else {'npcId','generationId','requestId','leaseToken'}
 require(set(value)==required,'NPC_GUARD_SCHEMA')
 require(all(isinstance(v,str) and v for v in value.values()),'NPC_GUARD_VALUES')
 require(re.fullmatch('[a-f0-9]{48}',value['generationId']) and re.fullmatch('[a-f0-9]{64}',value['lifecycleToken' if lifecycle else 'leaseToken']),'NPC_GUARD_FORMAT')
 require(stage is None or re.fullmatch(r'[A-Za-z0-9._:-]{1,180}',stage),'NPC_STAGE_ID_INVALID')
 return dict(value,stageRequestId=stage or value['requestId'])

@contextmanager
def request_scope(headers):
 token=GUARD.set(parse_headers(headers))
 try:yield
 finally:GUARD.reset(token)

def make_transport(config):
 require(config.get('ownershipMode') in (None,'npc-owned-v1'),'NPC_OWNERSHIP_MODE_INVALID')
 if config.get('ownershipMode')=='npc-owned-v1':
  from .pds_owned_transport import OwnedNPCTransport
  return OwnedNPCTransport(config,GUARD.get())
 from .filex_npc_store import NPCTransport
 return NPCTransport(config)
