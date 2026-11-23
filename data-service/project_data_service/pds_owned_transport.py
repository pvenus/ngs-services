"""Candidate PDS adapter: explicit request-bound guard, no implicit reacquisition.
Install alongside project_data_service only after every producer propagates its
original admission guard. Never obtain a fresh generation during late publish.
"""
import base64,hashlib
from urllib.parse import quote
from .filex_npc_store import NPCTransport
from .filex_read import require

class OwnedNPCTransport(NPCTransport):
 def __init__(self,config,guard=None):
  super().__init__(config);self.guard=guard
 def call(self,method,path,payload=None,missing=False):
  if method=='POST':
   c=super().call('GET','/v1/capabilities')
   require(c.get('npcOwnedSlots') is True and c.get('generationFence') is True,'NPC_OWNED_FENCE_CAPABILITY_REQUIRED')
  return super().call(method,path,payload,missing=missing)
 def frame_maintenance_capability(self):
  c=self.call('GET','/v1/capabilities')
  return {'releasedFrameCleanupLeaseV1':c.get('releasedFrameCleanupLeaseV1') is True}
 def frame_maintenance(self,action,payload):
  require(action in ('begin','reserve','release','finish'),'NPC_MAINTENANCE_ACTION')
  require(self.frame_maintenance_capability()['releasedFrameCleanupLeaseV1'],'NPC_MAINTENANCE_CAPABILITY_REQUIRED')
  return self.call('POST','/v1/generation/frame-maintenance/'+action,payload)
 def migration_prepare(self,plan):return self.call('POST','/v1/migration/prepare',plan)
 def migration_asset(self,request_id,token,asset_id,data):
  return self.call('POST','/v1/migration/asset',{'requestId':request_id,'token':token,'assetId':asset_id,'base64':base64.b64encode(data).decode()})
 def migration_commit(self,request_id,token):return self.call('POST','/v1/migration/commit',{'requestId':request_id,'token':token})
 def migration_activate(self,payload):return self.call('POST','/v1/migration/activate',payload)
 def migration_status(self,request_id):return self.call('GET','/v1/migration/status?requestId='+quote(request_id,safe=''))
 def staged_delete_fence(self,npc_id,generation_id,request_id):
  from urllib.parse import urlencode
  return self.call('GET','/v1/generation/staged-delete-fence?'+urlencode({'npcId':npc_id,'generationId':generation_id,'requestId':request_id}))
 def get_generation(self,npc_id):return self.call('GET','/v1/generation?npcId='+quote(npc_id,safe=''))
 def reserve_equivalent(self,npc,generation,parent,sha,reservation):
  return self.call('POST','/v1/generation/reserve-equivalent',{'npcId':npc,'generationId':generation,'parentRequestId':parent,'sha256':sha,'reservationId':reservation})
 def release_equivalent(self,npc,generation,parent,reservation,token):
  return self.call('POST','/v1/generation/release-equivalent',{'npcId':npc,'generationId':generation,'parentRequestId':parent,'reservationId':reservation,'reservationToken':token})
 def current_equivalent(self,npc_id,generation_id,parent_id,sha256):
  from urllib.parse import urlencode
  return self.call('GET','/v1/generation/equivalent?'+urlencode({'npcId':npc_id,'generationId':generation_id,'parentRequestId':parent_id,'sha256':sha256}))
 def publication_receipts(self,npc_id,generation_id,parent_id):
  from urllib.parse import urlencode
  return self.call('GET','/v1/generation/publications?'+urlencode({'npcId':npc_id,'generationId':generation_id,'parentRequestId':parent_id}))
 def bootstrap_generation(self,npc_id,source):return self.call('POST','/v1/generation/bootstrap',{'npcId':npc_id,'expectedSourceVersion':source})
 def admit(self,npc_id,generation_id,request_id):return self.call('POST','/v1/generation/admit',{'npcId':npc_id,'generationId':generation_id,'requestId':request_id})
 def backup_preview(self,backup_id):return self.call('GET','/v1/backup-preview?id='+quote(backup_id,safe=''))
 def abandon_creation(self,guard):return self.call('POST','/v1/generation/abandon-creation',{'guard':guard})
 def prepare_create(self,npc_id,request_id,source):return self.call('POST','/v1/generation/prepare-create',{'npcId':npc_id,'requestId':request_id,'expectedSourceVersion':source})
 def prepare_restore(self,npc_id,request_id,backup_id,source):return self.call('POST','/v1/generation/prepare-restore',{'npcId':npc_id,'requestId':request_id,'backupId':backup_id,'expectedSourceVersion':source})
 def restore_character(self,request_id,input_hash,backup_id,source,metadata):return self.call('POST','/v1/restore-character',{'requestId':request_id,'inputHash':input_hash,'backupId':backup_id,'expectedSourceVersion':source,'metadataAfter':metadata,'guard':self._guard()})
 def finish(self):return self.call('POST','/v1/generation/finish',{'guard':self._guard()})
 def _guard(self):
  require(isinstance(self.guard,dict) and all(self.guard.get(k)for k in ('npcId','generationId','requestId', 'lifecycleToken' if self.guard and self.guard.get('operation') in ('create','restore') else 'leaseToken')),'NPC_ADMISSION_GUARD_REQUIRED')
  return {k:v for k,v in self.guard.items() if k!='stageRequestId'} if self.guard.get('operation') in ('create','restore') else dict(self.guard)
 def batch(self,request_id,input_hash,plan):
  guard=self._guard()
  if guard.get('operation')=='create':
   return self.call('POST','/v1/create-character',{'requestId':request_id,'inputHash':input_hash,'files':plan['files'],'readSet':plan['readSet'],'expectedSourceVersion':guard['expectedSourceVersion'],'guard':guard})
  return self.call('POST','/v1/batch',{'requestId':request_id,'inputHash':input_hash,'files':plan['files'],'readSet':plan['readSet'],'guard':self._guard()})
 def asset_put(self,asset_id,data):
  result=self.call('POST','/v1/asset',{'assetId':asset_id,'base64':base64.b64encode(data).decode(),'guard':self._guard()})
  require(result.get('sha256')==hashlib.sha256(data).hexdigest(),'NPC_ASSET_HASH_MISMATCH');return result
 def stage_delete(self,npc_id,generation_id,request_id,source,*,replacement_evidence=None):
  payload={'npcId':npc_id,'generationId':generation_id,'requestId':request_id,'expectedSourceVersion':source}
  if replacement_evidence is not None:payload['replacementEvidence']=replacement_evidence
  return self.call('POST','/v1/generation/stage-delete',payload)
 def prepare_delete(self,npc_id,generation_id,request_id,external_hash):
  return self.call('POST','/v1/generation/prepare-delete',{'npcId':npc_id,'generationId':generation_id,'requestId':request_id,'externalOwnerManifestHash':external_hash})
 def prepare_canonical_delete(self,npc_id,generation_id,request_id):
  return self.call('POST','/v1/generation/prepare-delete',{'npcId':npc_id,'generationId':generation_id,'requestId':request_id,'deletionScope':'canonical-owner-generation.v1'})
 def delete_character(self,request_id,input_hash,character_id,source,metadata):
  return self.call('POST','/v1/delete-character',{'requestId':request_id,'inputHash':input_hash,'characterId':character_id,'expectedSourceVersion':source,'metadataAfter':metadata,'guard':self._guard()})
 def delete_prepared(self,request_id,input_hash,character_id,source,metadata,generation_id):
  # Deletion is fenced at zero leases: do not borrow/reacquire a worker lease.
  return self.call('POST','/v1/delete-character',{'requestId':request_id,'inputHash':input_hash,'characterId':character_id,'expectedSourceVersion':source,'metadataAfter':metadata,'guard':{'npcId':character_id,'generationId':generation_id,'requestId':request_id}})
 def workflow_plan_representative(self,binding,content,metadata):
  return self.call('POST','/v1/workflow/plan-representative',{'binding':binding,'content':content,'metadataAfter':metadata})
 def workflow_plan_current(self,stage,binding,content):
  return self.call('POST','/v1/workflow/plan-current',{'stage':stage,'binding':binding,'content':content})
 def workflow_plan_idle(self,binding,content):
  return self.call('POST','/v1/workflow/plan-idle',{'binding':binding,'content':content})
 def workflow_portrait_representative(self,binding,base64_png,*,mode='representative'):
  path={'representative':'portrait-representative','idle':'portrait-idle-reference','direction':'portrait-direction','combat':'portrait-combat-reference',
        'combat_direction':'portrait-combat-direction'}[mode]
  return self.call('POST','/v1/workflow/'+path,{'binding':binding,'base64':base64_png})
 def workflow_media(self,kind,action,binding,artifact=None):
  require((kind in ('animation','frames','portrait-pixel') and action in ('read','publish')) or
          (kind=='frames' and action=='asset' and binding.get('regeneration') is True),'WORKFLOW_MEDIA_RPC')
  value={'binding':binding} if action=='read' else {'binding':binding,'artifact':artifact}
  return self.call('POST','/v1/workflow/'+kind+('/'+action if action in ('read','asset') else ''),value)
 def workflow_preset(self,binding,files):
  return self.call('POST','/v1/workflow/preset',{'binding':binding,'files':files})
 def workflow_delete_character(self,binding,metadata,*,target=None):
  body={'binding':binding,'metadataAfter':metadata}
  if target is not None:body['target']=target
  return self.call('POST','/v1/workflow/delete-character',body)
 def get_deletion_receipt(self,request_id):
  r=self.receipt(request_id)
  require(isinstance(r,dict) and r.get('status')=='committed' and r.get('backupVerified') is True and r.get('requestId')==request_id and all(r.get(k)for k in ('generationId','ownerManifestHash','backupId','backupManifestHash')) and ((r.get('deletionScope')=='canonical-owner-generation.v1' and r.get('externalDataDisposition')=='preserved-uninspected' and 'externalOwnerManifestHash' not in r) or ('deletionScope' not in r and bool(r.get('externalOwnerManifestHash')))),'NPC_DELETION_RECEIPT_UNVERIFIED')
  return r

 def portrait_asset(self,asset_id):
  require(__import__('re').fullmatch(r'asset\.[a-f0-9]{48}',asset_id),'PORTRAIT_ASSET_ID')
  return self.call('GET','/v1/portrait-assets/'+asset_id,missing=True)
 def register_portrait_asset(self,metadata,data):
  import json,urllib.request
  from .filex_read import canonical
  envelope={'metadata':metadata,'guard':self._guard()}
  req=urllib.request.Request(self.endpoint+'/v1/portrait-assets/register',data=data,method='POST',headers={'Authorization':'Bearer '+self.token,'Content-Type':'image/png','X-Portrait-Metadata':base64.b64encode(canonical(envelope)).decode()})
  try:
   with self.op.open(req,timeout=60)as response:
    require(response.status==200,'PORTRAIT_REGISTER_HTTP');raw=response.read(32769)
  except __import__('urllib.error',fromlist=['HTTPError']).HTTPError as exc:
   if exc.code>=500:raise OSError('PORTRAIT_REGISTER_OUTCOME_UNCERTAIN')from None
   raise ValueError('PORTRAIT_REGISTER_HTTP_'+str(exc.code))from None
  require(len(raw)<=32768,'PORTRAIT_RECEIPT_LIMIT');return json.loads(raw)
