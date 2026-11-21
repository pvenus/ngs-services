"""PDS writes one complete current file through the Filex-side atomic API.
No version directories, content backups, history UI, direct filesystem access,
model calls or worker patches. Metadata hashes are concurrency checks only.
"""
import copy
from .filex_read import canonical,digest,require
from .filex_knowledge import ID,PREFIX,FilexKnowledge,valid_path,find_entry,entry_path,category_path

class FilexKnowledgeWriter:
 def __init__(self,transport_factory=None):
  if transport_factory is None:
   from .filex_cas_transport import FilexCASTransport
   transport_factory=FilexCASTransport
  self.factory=transport_factory
 def require_write_capability(self):
  with self.factory()as t:require(t.capabilities().get('atomicCAS')is True,'KNOWLEDGE_ATOMIC_CAS_TRANSPORT_REQUIRED')
  return True
 def snapshot(self):return FilexKnowledge(self.factory).snapshot()
 _path=staticmethod(valid_path)
 def recover(self,request_id,input_hash):
  with self.factory()as t:record=t.receipt(request_id)
  if record is None:return None
  require(record.get('inputHash')==input_hash,'KNOWLEDGE_IDEMPOTENCY_CONFLICT')
  if record.get('status','committed')!='committed':return None
  path=record.get('path') or record.get('key','').removeprefix(PREFIX)
  require(self._path(path),'KNOWLEDGE_RECEIPT_PATH_INVALID')
  s=self.snapshot();entry=s['documents'].get(path)
  # No retained bodies by user instruction. Unknown old outcomes must not replay.
  require(entry is not None and s['hashes'].get(path)==record['afterHash'],'KNOWLEDGE_PRIOR_RESULT_SUPERSEDED_NO_REPLAY')
  return {'requestId':request_id,'inputHash':input_hash,'entry':entry,'sourceVersion':s['sourceVersion'],'manifestHash':s.get('manifestHash',s['sourceVersion']),'verified':True,'ngsReplicaPulled':False,'fileHash':record['afterHash']}
 def apply(self,request,expected_source_version,selection,*,files):
  if request['operation']=='delete-category':
   require(request.get('projectId')=='binari','KNOWLEDGE_WRITE_SCOPE')
   from .knowledge_category_removal import apply
   return apply(self,request,expected_source_version,files)
  r=request;op=r['operation'];eid=r['entryId'];require(isinstance(eid,str)and ID.fullmatch(eid),'KNOWLEDGE_ENTRY_ID_INVALID')
  require(r.get('projectId')=='binari' and op in ('create','update','delete'),'KNOWLEDGE_WRITE_SCOPE')
  self.require_write_capability();ih=digest(r);old=self.recover(r['requestId'],ih)
  if old:return old
  s=self.snapshot();require(s['sourceVersion']==expected_source_version,'KNOWLEDGE_SOURCE_CHANGED')
  old_path,before=find_entry(s['documents'],eid)
  category=(before or r.get('entry',{})).get('categoryId')
  require(isinstance(category,str)and ID.fullmatch(category),'KNOWLEDGE_CATEGORY_ID_INVALID')
  require(s['documents'].get(category_path(category),{}).get('status')!='deleted','KNOWLEDGE_CATEGORY_DELETED')
  key=entry_path(category,eid)
  require((op=='create'and before is None and r['expectedVersion']==0)or(op!='create'and before is not None and before['version']==r['expectedVersion']),'KNOWLEDGE_VERSION_CONFLICT')
  require(isinstance(files,list)and len(files)==1 and isinstance(files[0],dict)and set(files[0])=={'path','body','expectedHash'},'KNOWLEDGE_FULL_FILE_REQUIRED')
  file=files[0];require(file['path']==key and file['expectedHash']==s['hashes'].get(key),'KNOWLEDGE_FILE_VERSION_CONFLICT');entry=copy.deepcopy(file['body'])
  require(isinstance(entry,dict)and entry.get('id')==eid and entry.get('version')==r['expectedVersion']+1 and isinstance(entry.get('updatedAt'),str),'KNOWLEDGE_FULL_FILE_INVALID')
  require(before is None or entry.get('categoryId')==before.get('categoryId'),'KNOWLEDGE_CATEGORY_CHANGE_DENIED')
  require(category_path(entry.get('categoryId',''))in s['documents'],'KNOWLEDGE_CATEGORY_MISSING')
  if op=='delete':
   require(eid!='laya-worker-registry','KNOWLEDGE_BOOTSTRAP_DEPENDENCY_REQUIRED')
   require(entry.get('status')=='deleted'and all(entry.get(k)==v for k,v in before.items()if k not in ('status','version','updatedAt')),'KNOWLEDGE_DELETE_CONTENT_LOSS')
  with self.factory()as t:
   t.compare_and_swap(PREFIX+key,canonical(before)if before else None,canonical(entry),request_id=r['requestId'],input_hash=ih,expected_source_version=expected_source_version)
  result=self.recover(r['requestId'],ih);require(result is not None,'KNOWLEDGE_COMMIT_OUTCOME_UNCERTAIN');return result
