"""Create one NPC through its existing lifecycle guard, never a full-body HTTP write."""
import copy,re
from datetime import datetime,timezone
from .filex_read import digest,require
from .filex_npc_files import encode,PREFIX
from .npc_ownership import GUARD

FIELDS={'requestId','character','expectedRevision','beforeDocumentHash','documentHash','inputHash'}
def create(authority,payload):
 require(isinstance(payload,dict) and set(payload)==FIELDS,'NPC_CREATE_CONTRACT')
 require(type(payload['expectedRevision']) is int and payload['expectedRevision']>=0,'NPC_CREATE_REVISION')
 require(all(isinstance(payload[k],str) and re.fullmatch('[a-f0-9]{64}',payload[k]) for k in ('inputHash','beforeDocumentHash','documentHash')),'NPC_CREATE_HASH')
 require(payload['inputHash']==digest({k:v for k,v in payload.items()if k!='inputHash'}),'NPC_CREATE_INPUT_HASH')
 char=payload['character'];guard=GUARD.get()
 require(isinstance(char,dict) and isinstance(guard,dict) and guard.get('operation')=='create' and guard.get('npcId')==char.get('id'),'NPC_CREATE_GUARD_REQUIRED')
 require(isinstance(payload['requestId'],str) and re.fullmatch(r'[A-Za-z0-9._:-]{1,200}',payload['requestId']) and char.get('creationBasis',{}).get('requestId')==payload['requestId'],'NPC_CREATE_REQUEST_BINDING')
 contract={'requestId':payload['requestId'],'inputHash':payload['inputHash'],'characterId':char['id'],'characterHash':digest(char),'beforeDocumentHash':payload['beforeDocumentHash'],'documentHash':payload['documentHash'],'generationEpoch':guard['requestId'],'generationId':guard['generationId'],'ownershipGuardHash':digest({k:v for k,v in guard.items()if k!='stageRequestId'})}
 def result(receipt):
  require(receipt.get('requestId')==guard['requestId'] and receipt.get('inputHash')==payload['inputHash'] and receipt.get('status')=='committed' and receipt.get('generationId')==guard['generationId'],'NPC_CREATE_RECEIPT_BINDING')
  marker=receipt.get('resultMetadata',{})
  require(marker.get('scopedCreation')==contract and marker.get('revision')==payload['expectedRevision']+1 and marker.get('documentHash')==payload['documentHash'],'NPC_CREATE_RECEIPT_METADATA')
  return {**contract,'revision':marker['revision'],'savedAt':marker['savedAt'],'note':marker['note'],'authority':'filex','verified':True,'publicationReceipt':receipt}
 # Receipt first: a later unrelated NPC write cannot invalidate an exact replay.
 with authority.store.factory()as transport:prior=transport.receipt(guard['requestId'])
 if prior is not None:return result(prior)
 snapshot,marker,before=authority._read()
 require(snapshot['sourceVersion']==guard['expectedSourceVersion'],'NPC_CREATE_SOURCE_CHANGED')
 require(marker['revision']==payload['expectedRevision'] and digest(before)==payload['beforeDocumentHash'],'NPC_CREATE_SOURCE_CHANGED')
 require(not any(c.get('id')==char['id']for c in before['characters']),'NPC_CREATE_ALREADY_EXISTS')
 document=copy.deepcopy(before);document['characters'].append(copy.deepcopy(char));document['generationEpoch']=guard['requestId']
 from .domain_contracts.legacy_http import validate_npcs
 document=validate_npcs(document,authority.asset_get)
 require(digest(document)==payload['documentHash'],'NPC_CREATE_DOCUMENT_HASH')
 after=encode(document)
 after['metadata.json']['_pdsCurrent']={'revision':marker['revision']+1,'savedAt':datetime.now(timezone.utc).isoformat(),'note':'NPC scoped creation','operationHash':payload['inputHash'],'documentHash':payload['documentHash'],'scopedCreation':contract}
 # encode may touch only new target files and the root ordering metadata.
 changed={key for key in set(after)|set(snapshot['documents']) if after.get(key)!=snapshot['documents'].get(key)}
 require(all(key=='metadata.json' or key.startswith(char['id']+'/') for key in changed),'NPC_CREATE_UNRELATED_FILE_CHANGE')
 hashes={key:snapshot['hashes'].get(key)for key in set(after)|set(snapshot['documents'])}
 outcome=authority.store.apply(guard['requestId'],payload['inputHash'],before=snapshot['documents'],after=after,expected_hashes=hashes)
 return result(outcome['receipt'])
