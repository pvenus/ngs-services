"""One deterministic knowledge worker output: complete files, never storage patches."""
import copy,re
from datetime import datetime,timezone
from .domain_contracts.knowledge_catalog import KnowledgeError
from .filex_knowledge import find_entry,entry_path,category_path
FIELDS={'title','purpose','content','data','triggers','authority','status','maxReadChars'}
def build_files(request,snapshot):
 if request["operation"]=="delete-category":
  from .knowledge_category_removal import build
  return build(request,snapshot)
 path,current=find_entry(snapshot['documents'],request['entryId']);op=request['operation']
 if op=='create':
  result=copy.deepcopy(request['entry'])
  if not {'id','categoryId','type','title','purpose'}<=set(result):raise KnowledgeError('KNOWLEDGE_ENTRY_REQUIRED_FIELDS')
  path=entry_path(str(result['categoryId']),request['entryId'])
  if category_path(str(result['categoryId']))not in snapshot['documents']:raise KnowledgeError('KNOWLEDGE_CATEGORY_NOT_FOUND')
  for key,value in {'content':'','data':{},'triggers':[],'authority':'supporting','status':'draft','maxReadChars':12000}.items():result.setdefault(key,value)
 else:
  if not current:raise KnowledgeError('KNOWLEDGE_ENTRY_MISSING')
  result=copy.deepcopy(current)
  if op=='delete':result['status']='deleted'
  else:
   changes=request.get('changes')
   if not isinstance(changes,dict)or not changes or set(changes)-FIELDS:raise KnowledgeError('KNOWLEDGE_FIELDS_INVALID')
   result.update(copy.deepcopy(changes))
 if result.get('id')!=request['entryId'] or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]{0,127}',request['entryId']):raise KnowledgeError('KNOWLEDGE_ENTRY_ID_INVALID')
 if any(not isinstance(result.get(k),str)for k in ('type','title','purpose','content'))or not result['title'].strip()or not result['purpose'].strip():raise KnowledgeError('KNOWLEDGE_ENTRY_TEXT_INVALID')
 if result['status']not in ('active','draft','legacy','deleted')or result['authority']not in ('authoritative','supporting','reference'):raise KnowledgeError('KNOWLEDGE_ENTRY_POLICY_INVALID')
 if not isinstance(result['data'],dict)or not isinstance(result['triggers'],list)or any(not isinstance(t,str)for t in result['triggers']):raise KnowledgeError('KNOWLEDGE_ENTRY_DATA_INVALID')
 if current and not snapshot.get('hashes',{}).get(path):raise KnowledgeError('KNOWLEDGE_EXPECTED_HASH_REQUIRED')
 result['version']=request['expectedVersion']+1;result['updatedAt']=datetime.now(timezone.utc).isoformat()
 return [{'path':path,'body':result,'expectedHash':snapshot.get('hashes',{}).get(path)}]
