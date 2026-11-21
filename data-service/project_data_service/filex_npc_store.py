"""Opt-in PDS NPC Filex adapter. No production routes/configuration are changed.
Only sidecar-cooperating readers have batch isolation. Never use raw Filex
reads for a coherent NPC snapshot during a multi-file update.
"""
from .filex_cas_transport import FilexCASTransport
from .filex_npc_files import PREFIX, changed_files, decode
from .filex_read import digest, require

class NPCTransport(FilexCASTransport):
 def __init__(self,config):
  # Deliberately no default knowledge configuration or implicit authority expansion.
  require(config.get('scope')=='npc','NPC_EXPLICIT_TRANSPORT_SCOPE_REQUIRED')
  super().__init__(config)
 def batch(self,request_id,input_hash,plan):
  return self.call('POST','/v1/batch',{'requestId':request_id,'inputHash':input_hash,'files':plan['files'],'readSet':plan['readSet']})

class FilexNPCStore:
 def __init__(self,transport_factory):self.factory=transport_factory
 def snapshot(self):
  with self.factory() as t:
   capabilities=t.capabilities()
   require(capabilities.get('lockedBatch') is True and capabilities.get('namespace')==PREFIX,'NPC_BATCH_CAPABILITY_REQUIRED')
   s=t.snapshot()
  require(s.get('hashMode')=='canonical-json','NPC_HASH_MODE_INVALID')
  require(s.get('hashes')=={p:digest(v)for p,v in s['documents'].items()} and s.get('sourceVersion')==digest(s['hashes']),'NPC_SNAPSHOT_HASH_MISMATCH')
  changed_files(s['documents'],s['documents'],s['hashes']) # validate paths
  return s
 def current(self):
  s=self.snapshot()
  return {'document':decode(s['documents']),'sourceVersion':s['sourceVersion'],'hashes':s['hashes']}
 def apply(self,request_id,input_hash,*,before,after,expected_hashes):
  require(isinstance(request_id,str) and bool(request_id) and isinstance(input_hash,str) and bool(input_hash),'NPC_REQUEST_BINDING_REQUIRED')
  plan=changed_files(before,after,expected_hashes)
  require(bool(plan['files']),'NPC_NO_CHANGED_FILES')
  with self.factory() as t:
   c=t.capabilities();require(c.get('lockedBatch') is True and c.get('namespace')==PREFIX,'NPC_BATCH_CAPABILITY_REQUIRED')
   receipt=t.batch(request_id,input_hash,plan)
  require(receipt.get('requestId')==request_id and receipt.get('inputHash')==input_hash and receipt.get('status')=='committed','NPC_COMMIT_OUTCOME_UNCERTAIN')
  expected={f['path']:digest(f['body'])for f in plan['files']}
  require(receipt.get('hashes')==expected,'NPC_RECEIPT_HASH_MISMATCH')
  s=self.snapshot()
  current_matches=all(s['hashes'].get(k.removeprefix(PREFIX))==v for k,v in expected.items())
  return {'receipt':receipt,'sourceVersion':s['sourceVersion'],'verified':True,'ngsReplicaPulled':False,'changedFiles':len(expected),'currentMatches':current_matches}

# Byte originals travel through authenticated PDS transport, never host paths.
def _asset_get(self,asset_id):
 import base64,hashlib,urllib.parse
 result=self.call('GET','/v1/asset?id='+urllib.parse.quote(asset_id,safe=''))
 data=base64.b64decode(result['base64'],validate=True)
 require(hashlib.sha256(data).hexdigest()==asset_id.split('.')[0],'NPC_ASSET_HASH_MISMATCH')
 return data
def _asset_put(self,asset_id,data):
 import base64,hashlib
 result=self.call('POST','/v1/asset',{'assetId':asset_id,'base64':base64.b64encode(data).decode()})
 require(result.get('sha256')==hashlib.sha256(data).hexdigest(),'NPC_ASSET_HASH_MISMATCH')
 return result
def _delete_character(self,request_id,input_hash,character_id,source,metadata):
 return self.call('POST','/v1/delete-character',{'requestId':request_id,'inputHash':input_hash,'characterId':character_id,'expectedSourceVersion':source,'metadataAfter':metadata})
NPCTransport.asset_get=_asset_get
NPCTransport.asset_put=_asset_put
NPCTransport.delete_character=_delete_character
