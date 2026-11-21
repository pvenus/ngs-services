"""Typed internal file transport. Request sees authority metadata, never bytes.

Default-off: a trusted host explicitly composes this service from its existing
media authority callback and existing owned Filex factory. No tokens or grants
are created here; this is not a browser/public ingress.
"""
import hashlib,json,struct
from .filex_read import canonical,digest,require
from .npc_owned_operations import OwnedNPCOperations
from .direct_guard import ingress,INGRESS
PATH='/v1/npcs/ownership/operations'
AUTH_PATH='/v1/publications/owned-operation/authorize'
# Reuse the existing Request publication-authority JSON envelope ceiling.
MAX_META=1024*1024

def assets(value):
 result=set()
 def visit(v):
  if isinstance(v,dict):
   for k,x in v.items():
    if k in ('assetId','alphaAssetId') and isinstance(x,str):result.add(x)
    visit(x)
  elif isinstance(v,list):
   for x in v:visit(x)
 visit(value);return sorted(result)

def decode(raw):
 require(type(raw)is bytes and 4<len(raw),'OWNED_BODY_LIMIT')
 size=struct.unpack('>I',raw[:4])[0]
 require(0<size<=MAX_META and 4+size<=len(raw),'OWNED_METADATA_LIMIT')
 envelope=json.loads(raw[4:4+size]);payload=raw[4+size:]
 require(type(envelope)is dict and set(envelope)=={'claim','action','metadata'} and type(envelope['claim'])is dict and type(envelope['metadata'])is dict,'OWNED_ENVELOPE')
 return envelope,payload

class OwnedOperationsHTTP:
 def __init__(self,authority,transport_factory):
  require(callable(authority)and callable(transport_factory),'OWNED_HOST_PORTS')
  self.authority,self.factory=authority,transport_factory
 def dispatch(self,raw):
  e,payload=decode(raw);claim=e['claim'];action=e['action'];metadata=e['metadata'];plan=None
  if action=='asset':
   from .media_validation import png
   png(payload);sha=hashlib.sha256(payload).hexdigest()
   require(metadata=={'assetId':sha+'.png','sha256':sha,'bytes':len(payload)},'OWNED_ASSET_METADATA')
  elif action=='commit':
   plan=json.loads(payload)
   require(type(plan)is dict and set(plan)=={'inputHash','files','readSet'} and type(plan['files'])is list and len(plan['files'])==1 and type(plan['files'][0])is dict,'OWNED_BATCH')
   f=plan['files'][0]
   require(set(f)=={'path','body','expectedHash'}and type(f['body'])is dict,'OWNED_BATCH')
   require(metadata=={'path':f['path'],'inputHash':plan['inputHash'],'manifestHash':digest(f['body']),'assetIds':assets(f['body'])},'OWNED_BATCH_METADATA')
  else:require(action=='receipt'and metadata=={}and payload==b'','OWNED_ACTION')
  binding=self.authority('POST',AUTH_PATH,e)
  require(type(binding)is dict and set(binding)=={'guard','stageRequestId','inputHash','allowedPath','action','metadata'},'OWNED_AUTHORITY_RESPONSE')
  guard=INGRESS.get()
  require(type(guard)is dict and binding['guard']=={k:v for k,v in guard.items()if k!='stageRequestId'} and binding['stageRequestId']==guard['stageRequestId']==claim.get('request_id') and binding['action']==action and binding['metadata']==metadata,'OWNED_AUTHORITY_BINDING')
  op=OwnedNPCOperations(self.factory,guard=binding['guard'],stage_request_id=binding['stageRequestId'])
  op._check_path(binding['allowedPath'])
  if action=='asset':result=op.upload_asset(metadata['assetId'],payload)
  elif action=='commit':
   require(plan['inputHash']==binding['inputHash']and plan['files'][0]['path']==binding['allowedPath']and type(plan['readSet'])is dict and set(plan['readSet'])=={binding['allowedPath']},'OWNED_BATCH_SCOPE')
   result=op.commit(binding['stageRequestId'],plan['inputHash'],plan['files'],plan['readSet'])
  else:
   result=op.receipt(binding['stageRequestId'])
   if result is not None:require(result['inputHash']==binding['inputHash']and set(result['hashes'])=={binding['allowedPath']},'OWNED_RECEIPT_SCOPE')
  # A stale post-check is an uncertain result, never authorization to regenerate.
  require(self.authority('POST',AUTH_PATH,e)==binding,'OWNED_AUTHORITY_CHANGED')
  return result

def serve(handler,service):
 with ingress(handler):
  require(handler.command=='POST'and handler.path==PATH and handler.headers.get('Content-Type')=='application/octet-stream','OWNED_HTTP')
  n=handler.headers.get('Content-Length','');require(n.isdecimal()and 0<int(n),'OWNED_BODY_LIMIT')
  handler.connection.settimeout(60);raw=handler.rfile.read(int(n));require(len(raw)==int(n),'OWNED_TRUNCATED')
  return handler.respond(200,{'result':service.dispatch(raw)})

def configure(server,*,enabled=False,host='127.0.0.1'):
 """Explicit local startup feature opt-in; reuse installed identities/factory."""
 require(type(enabled)is bool,'OWNED_OPT_IN')
 if not enabled:return
 require(host=='127.0.0.1','OWNED_LOCAL_ONLY')
 from .filex_npc_authority import FilexCurrentNPC
 media=getattr(server,'media_artifacts',None)
 source=getattr(getattr(getattr(server,'app',None),'domains',None),'npcs',None)
 require(media is not None and callable(getattr(media,'authority',None))and isinstance(source,FilexCurrentNPC),'OWNED_EXISTING_SERVICES_REQUIRED')
 require(callable(source.store.factory),'OWNED_HOST_PORTS')
 server.owned_npc_operations=OwnedOperationsHTTP(media.authority,source.store.factory)
