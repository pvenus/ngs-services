"""Logical removal of a whole knowledge classification; child bytes retained."""
import copy
from datetime import datetime,timezone
from .filex_knowledge import category_path,find_entry
from .filex_read import require

def current(documents,request):
 if request['operation']=='delete-category':return documents.get(category_path(request['entryId']))
 return find_entry(documents,request['entryId'])[1]

def build(request,snapshot):
 cid=request['entryId'];path=category_path(cid);before=snapshot['documents'].get(path)
 require(before is not None,'KNOWLEDGE_CATEGORY_NOT_FOUND')
 require(before.get('status')!='deleted','KNOWLEDGE_CATEGORY_ALREADY_DELETED')
 children=[v for p,v in snapshot['documents'].items() if p.startswith('categories/'+cid+'/entries/')]
 require(not any(v.get('id')=='laya-worker-registry' for v in children),'KNOWLEDGE_BOOTSTRAP_CATEGORY_REQUIRED')
 result=copy.deepcopy(before);result.update(status='deleted',version=request['expectedVersion']+1,updatedAt=datetime.now(timezone.utc).isoformat())
 return [{'path':path,'body':result,'expectedHash':snapshot['hashes'][path]}]

def apply(writer,request,source,files):
 from .filex_read import canonical,digest
 from .filex_knowledge import PREFIX
 writer.require_write_capability();ih=digest(request);old=writer.recover(request['requestId'],ih)
 if old:return old
 snapshot=writer.snapshot();require(snapshot['sourceVersion']==source,'KNOWLEDGE_SOURCE_CHANGED')
 expected=build(request,snapshot)[0];before=snapshot['documents'][expected['path']]
 require(before.get('version',0)==request['expectedVersion'],'KNOWLEDGE_VERSION_CONFLICT')
 require(len(files)==1 and files[0]['path']==expected['path'] and files[0]['expectedHash']==expected['expectedHash'],'KNOWLEDGE_CATEGORY_DELETE_TARGET_MISMATCH')
 body=files[0]['body'];require(body.get('status')=='deleted' and body.get('version')==request['expectedVersion']+1 and isinstance(body.get('updatedAt'),str),'KNOWLEDGE_CATEGORY_DELETE_INVALID')
 require({k:v for k,v in body.items() if k not in ('status','version','updatedAt')}=={k:v for k,v in before.items() if k not in ('status','version','updatedAt')},'KNOWLEDGE_CATEGORY_DELETE_CONTENT_LOSS')
 with writer.factory() as transport:transport.compare_and_swap(PREFIX+expected['path'],canonical(before),canonical(body),request_id=request['requestId'],input_hash=ih,expected_source_version=source)
 result=writer.recover(request['requestId'],ih);require(result is not None,'KNOWLEDGE_COMMIT_OUTCOME_UNCERTAIN');return result
