"""Internal server-only operations on an already admitted NPC.

The caller supplies its trusted original guard and selected stage. This module
does not admit/refresh ownership, select work, inspect business schemas, open
an ingress route, or expose transport credentials to a worker.
"""
import copy
import hashlib
import re
from contextlib import contextmanager

from .filex_npc_files import PREFIX
from .filex_read import digest, require
from .pds_owned_transport import OwnedNPCTransport

_OWNER=re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')
_RID=re.compile(r'[A-Za-z0-9._:-]{1,180}')
_GEN=re.compile(r'[a-f0-9]{48}')
_SHA=re.compile(r'[a-f0-9]{64}')
_ASSET=re.compile(r'[a-f0-9]{64}\.(?:png|jpg|webp|mp4)')
_GUARD_KEYS={'npcId','generationId','requestId','leaseToken'}
_RECEIPT_KEYS={'requestId','inputHash','payloadHash','status','hashes','resultMetadata',
               'generationId','parentRequestId','stageRequestId'}


def _matches(pattern,value):
 return type(value)is str and pattern.fullmatch(value) is not None


class OwnedNPCOperations:
 def __init__(self,transport_factory,*,guard,stage_request_id):
  require(callable(transport_factory),'NPC_OWNED_OPERATION_TRANSPORT')
  require(type(guard)is dict and set(guard)==_GUARD_KEYS and
          all(_matches(p,guard[k])for k,p in {'npcId':_OWNER,'generationId':_GEN,
                                              'requestId':_RID,'leaseToken':_SHA}.items()),
          'NPC_OWNED_OPERATION_GUARD')
  require(_matches(_RID,stage_request_id),'NPC_OWNED_OPERATION_STAGE')
  self._factory=transport_factory
  self._guard={**guard,'stageRequestId':stage_request_id}
  self._prefix=PREFIX+guard['npcId']+'/'

 @contextmanager
 def _transport(self):
  with self._factory() as transport:
   require(isinstance(transport,OwnedNPCTransport),'NPC_OWNED_OPERATION_TRANSPORT')
   transport.guard=dict(self._guard)
   yield transport

 def _check_stage(self,request_id):
  require(_matches(_RID,request_id) and request_id==self._guard['stageRequestId'],'NPC_OWNED_OPERATION_STAGE')

 def _check_path(self,path):
  require(type(path)is str and path.startswith(self._prefix),'NPC_OWNED_OPERATION_SCOPE')
  parts=path[len(self._prefix):].split('/')
  require(all(_matches(_OWNER,part)for part in parts) and parts[-1].endswith('.json'),
          'NPC_OWNED_OPERATION_SCOPE')

 def upload_asset(self,asset_id,data):
  require(_matches(_ASSET,asset_id) and type(data)is bytes and data and
          hashlib.sha256(data).hexdigest()==asset_id.split('.',1)[0],'NPC_OWNED_OPERATION_ASSET')
  with self._transport()as transport:
   return transport.asset_put(asset_id,data)

 def _receipt_shape(self,value,request_id):
  require(type(value)is dict and set(value)==_RECEIPT_KEYS and
          value['requestId']==value['stageRequestId']==request_id and
          value['generationId']==self._guard['generationId'] and value['parentRequestId']==self._guard['requestId']
          and value['status']=='committed' and all(_matches(_SHA,value[k])for k in ('inputHash','payloadHash'))
          and type(value['hashes'])is dict and value['hashes'] and
          all(_matches(_SHA,sha)for sha in value['hashes'].values()),'NPC_OWNED_OPERATION_RECEIPT')
  for path in value['hashes']:self._check_path(path)
  return value

 def commit(self,request_id,input_hash,files,read_set):
  self._check_stage(request_id)
  require(_matches(_SHA,input_hash) and type(files)is list and files and type(read_set)is dict,
          'NPC_OWNED_OPERATION_BATCH')
  read_set=dict(read_set);files=copy.deepcopy(files)
  for path,expected in read_set.items():
   self._check_path(path)
   require(expected is None or _matches(_SHA,expected),'NPC_OWNED_OPERATION_BATCH')
  expected_writes={}
  for file in files:
   require(type(file)is dict and set(file)=={'path','body','expectedHash'} and type(file['body'])is dict,
           'NPC_OWNED_OPERATION_BATCH')
   path=file['path'];self._check_path(path)
   require(path not in expected_writes and path in read_set and file['expectedHash']==read_set[path],
           'NPC_OWNED_OPERATION_BATCH')
   expected_writes[path]=digest(file['body'])
  plan={'files':files,'readSet':read_set}
  with self._transport()as transport:
   result=self._receipt_shape(transport.batch(request_id,input_hash,plan),request_id)
  require(result['inputHash']==input_hash and result['payloadHash']==digest(plan) and result['hashes']==expected_writes,
          'NPC_OWNED_OPERATION_RECEIPT')
  return result

 def receipt(self,request_id):
  """Read only via the Filex owner/generation/parent committed receipt set."""
  self._check_stage(request_id)
  g=self._guard
  with self._transport()as transport:
   value=transport.publication_receipts(g['npcId'],g['generationId'],g['requestId'])
  require(type(value)is dict and set(value)=={'npcId','generationId','parentRequestId','receipts'} and
          value['npcId']==g['npcId'] and value['generationId']==g['generationId'] and
          value['parentRequestId']==g['requestId'] and type(value['receipts'])is list,
          'NPC_OWNED_OPERATION_RECEIPT')
  matches=[]
  for row in value['receipts']:
   require(type(row)is dict,'NPC_OWNED_OPERATION_RECEIPT')
   if row.get('requestId')!=request_id and row.get('stageRequestId')!=request_id:continue
   require(row.get('npcId')==g['npcId'] and row.get('verified')is True,'NPC_OWNED_OPERATION_RECEIPT')
   matches.append(self._receipt_shape({k:v for k,v in row.items()if k not in ('npcId','verified')},request_id))
  require(len(matches)<=1,'NPC_OWNED_OPERATION_RECEIPT')
  return matches[0] if matches else None
