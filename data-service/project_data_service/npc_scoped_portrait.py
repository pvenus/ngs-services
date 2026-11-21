"""Request-bound portrait metadata publication, without whole-document HTTP bodies."""
import copy,re
from datetime import datetime,timezone
from .filex_read import digest,require
from .filex_npc_files import encode
from .npc_ownership import GUARD
FIELDS={'requestId','npcId','posePreset','expectedRevision','beforeDocumentHash','documentHash','note','patch','inputHash'}

def publish(authority,payload):
 require(isinstance(payload,dict) and set(payload)==FIELDS,'NPC_PORTRAIT_CONTRACT')
 require(type(payload['expectedRevision'])is int and payload['expectedRevision']>=0,'NPC_PORTRAIT_REVISION')
 require(all(isinstance(payload[k],str)and re.fullmatch('[a-f0-9]{64}',payload[k])for k in ('inputHash','beforeDocumentHash','documentHash')),'NPC_PORTRAIT_HASH')
 require(payload['inputHash']==digest({k:v for k,v in payload.items()if k!='inputHash'}),'NPC_PORTRAIT_INPUT_HASH')
 require(isinstance(payload['note'],str)and 3<=len(payload['note'].strip())<=300 and payload['note']==payload['note'].strip(),'NPC_PORTRAIT_NOTE')
 require(all(isinstance(payload[k],str)and re.fullmatch(r'[A-Za-z0-9._:-]{1,200}',payload[k])for k in ('requestId','npcId')),'NPC_PORTRAIT_IDS')
 pose=payload['posePreset'];patch=payload['patch'];guard=GUARD.get()
 require(pose in (None,'representative','combat','idle') and isinstance(patch,dict) and {'portraits','activePortraitId'}<=set(patch) and not set(patch)-{'portraits','activePortraitId','portraitSets'},'NPC_PORTRAIT_PATCH')
 require(isinstance(guard,dict)and guard.get('operation')is None and guard.get('npcId')==payload['npcId'] and payload['requestId']in (guard.get('requestId'),guard.get('stageRequestId')) and guard.get('leaseToken'),'NPC_PORTRAIT_GUARD_REQUIRED')
 contract={k:payload[k]for k in ('requestId','npcId','posePreset','inputHash','beforeDocumentHash','documentHash')}
 contract.update(generationId=guard['generationId'],ownershipGuardHash=digest(guard),patchHash=digest(patch))
 commit_id='npc.portrait.'+digest([payload['requestId'],payload['npcId'],pose,guard['requestId'],guard.get('stageRequestId')])
 def result(receipt):
  require(receipt.get('requestId')==commit_id and receipt.get('inputHash')==payload['inputHash'] and receipt.get('status')=='committed','NPC_PORTRAIT_RECEIPT_BINDING')
  marker=receipt.get('resultMetadata',{})
  require(marker.get('scopedPortrait')==contract and marker.get('revision')==payload['expectedRevision']+1 and marker.get('documentHash')==payload['documentHash'] and marker.get('note')==payload['note'],'NPC_PORTRAIT_RECEIPT_METADATA')
  return {**contract,'revision':marker['revision'],'savedAt':marker['savedAt'],'note':marker['note'],'authority':'filex','verified':True,'publicationReceipt':receipt}
 with authority.store.factory()as transport:prior=transport.receipt(commit_id)
 if prior is not None:return result(prior)
 snapshot,marker,before=authority._read()
 require(marker['revision']==payload['expectedRevision'] and digest(before)==payload['beforeDocumentHash'],'NPC_PORTRAIT_SOURCE_CHANGED')
 matches=[c for c in before['characters']if c.get('id')==payload['npcId']]
 require(len(matches)==1,'NPC_PORTRAIT_TARGET_MISSING');old=matches[0]
 old_slots=old.get('portraitSets',{});new_slots=patch.get('portraitSets',old_slots)
 require(isinstance(new_slots,dict)and isinstance(old_slots,dict),'NPC_PORTRAIT_SLOTS')
 if pose is None:
  require(new_slots==old_slots,'NPC_PORTRAIT_UNRELATED_SLOT_CHANGE')
 else:
  require(pose in new_slots and {k:v for k,v in new_slots.items()if k!=pose}=={k:v for k,v in old_slots.items()if k!=pose},'NPC_PORTRAIT_UNRELATED_SLOT_CHANGE')
  require(isinstance(new_slots[pose],dict)and new_slots[pose].get('requestId')==payload['requestId'],'NPC_PORTRAIT_SLOT_REQUEST_BINDING')
  if pose!='representative':
   require(patch['activePortraitId']==old.get('activePortraitId'),'NPC_PORTRAIT_UNRELATED_ACTIVE_CHANGE')
   require(new_slots[pose].get('referencePortraitAssetId')==old_slots.get('representative',{}).get('portraitAssetId') and new_slots[pose].get('referencePortraitAssetId') is not None,'NPC_PORTRAIT_REFERENCE_CHANGED')
 # Old inventory outside the selected slot is immutable. Retiring entries shared
 # by other slots is forbidden; only the selected pair's new assets may be added.
 prior_pair=old_slots.get(pose,{})if pose else{}
 old_pair={prior_pair.get(k)for k in ('portraitAssetId','pixelAssetId')}-{None}
 protected={v.get(k)for key,v in old_slots.items()if key!=pose for k in ('portraitAssetId','pixelAssetId')}-{None}
 new_pair=new_slots.get(pose,{})if pose else{}
 new_assets=({new_pair.get(k)for k in ('portraitAssetId','pixelAssetId')}if pose else{patch.get('activePortraitId')})-{None}
 require(isinstance(old.get('portraits'),list)and isinstance(patch['portraits'],list),'NPC_PORTRAIT_INVENTORY')
 old_rows={r['assetId']:r for r in old['portraits']};new_rows={r['assetId']:r for r in patch['portraits']}
 require(len(new_rows)==len(patch['portraits']),'NPC_PORTRAIT_DUPLICATE_ASSET')
 required=set(old_rows)-(old_pair-protected)
 require(required<=set(new_rows) and all(new_rows.get(k)==old_rows[k]for k in required-new_assets) and not(set(new_rows)-set(old_rows)-new_assets),'NPC_PORTRAIT_UNRELATED_INVENTORY_CHANGE')
 document=copy.deepcopy(before);target=next(c for c in document['characters']if c['id']==payload['npcId']);target.update(copy.deepcopy(patch))
 from .domain_contracts.legacy_http import validate_npcs
 validate_npcs(document,authority.asset_get)
 require(digest(document)==payload['documentHash'],'NPC_PORTRAIT_DOCUMENT_HASH')
 after=encode(document)
 after['metadata.json']['_pdsCurrent']={'revision':marker['revision']+1,'savedAt':datetime.now(timezone.utc).isoformat(),'note':payload['note'],'operationHash':payload['inputHash'],'documentHash':payload['documentHash'],'scopedPortrait':contract}
 changed={k for k in set(after)|set(snapshot['documents'])if after.get(k)!=snapshot['documents'].get(k)}
 require(changed <= {'metadata.json',payload['npcId']+'/metadata.json'},'NPC_PORTRAIT_UNRELATED_FILE_CHANGE')
 hashes={k:snapshot['hashes'].get(k)for k in set(after)|set(snapshot['documents'])}
 outcome=authority.store.apply(commit_id,payload['inputHash'],before=snapshot['documents'],after=after,expected_hashes=hashes)
 return result(outcome['receipt'])
