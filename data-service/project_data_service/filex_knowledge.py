"""Read single current knowledge files through the Filex-side atomic service.
No version directories, source history, backup writes, or SQLite fallback.
"""
import re
from .filex_read import digest,require
from .filex_cas_transport import FilexCASTransport
PREFIX='canonical-files/binari/knowledge/'
HEX=re.compile(r'[a-f0-9]{64}\Z')
ID=re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')
def entry_path(category_id,entry_id):
 require(all(isinstance(v,str)and ID.fullmatch(v)for v in (category_id,entry_id)),'KNOWLEDGE_PATH_ID_INVALID')
 return 'categories/'+category_id+'/entries/'+entry_id+'.json'
def category_path(category_id):
 require(isinstance(category_id,str)and ID.fullmatch(category_id),'KNOWLEDGE_PATH_ID_INVALID')
 return 'categories/'+category_id+'/metadata.json'
def valid_path(path):return path=='overview.json' or bool(re.fullmatch(r'categories/[A-Za-z0-9][A-Za-z0-9._-]{0,127}/(?:metadata|entries/[A-Za-z0-9][A-Za-z0-9._-]{0,127})\.json',path))
def find_entry(documents,entry_id):
 require(isinstance(entry_id,str)and ID.fullmatch(entry_id),'KNOWLEDGE_ENTRY_ID_INVALID')
 matches=[(p,v)for p,v in documents.items()if '/entries/'in p and p.endswith('/'+entry_id+'.json')]
 require(len(matches)<=1,'KNOWLEDGE_DUPLICATE_ENTRY_ID')
 return matches[0]if matches else(None,None)
class FilexKnowledge:
 def __init__(self,transport_factory=FilexCASTransport):self.factory=transport_factory
 def snapshot(self,expected_version=None,paths=None):
  with self.factory()as transport:snapshot=transport.snapshot()
  require(isinstance(snapshot,dict),'KNOWLEDGE_SNAPSHOT_INVALID')
  documents=snapshot.get('documents');hashes=snapshot.get('hashes');version=snapshot.get('sourceVersion')
  require(isinstance(documents,dict)and isinstance(hashes,dict)and set(documents)==set(hashes),'KNOWLEDGE_SNAPSHOT_FILES_INVALID')
  require(isinstance(version,str)and HEX.fullmatch(version)and digest(hashes)==version and snapshot.get('manifestHash')==version,'KNOWLEDGE_SNAPSHOT_HASH_INVALID')
  if expected_version is not None:require(version==expected_version,'KNOWLEDGE_SOURCE_CHANGED')
  for path,value in documents.items():
   require(valid_path(path),'KNOWLEDGE_FILE_PATH_INVALID')
   require(isinstance(value,dict)and digest(value)==hashes[path],'KNOWLEDGE_FILE_HASH_MISMATCH')
   if path!='overview.json':
    require(value.get('id')==(path.split('/')[1]if path.endswith('/metadata.json')else path.rsplit('/',1)[1][:-5]),'KNOWLEDGE_FILE_ID_MISMATCH')
    if '/entries/'in path:require(value.get('categoryId')==path.split('/')[1],'KNOWLEDGE_CATEGORY_PATH_MISMATCH')
  require('overview.json'in documents,'KNOWLEDGE_OVERVIEW_MISSING')
  # Optional projection never changes authoritative file bytes or hashes.
  return {'sourceVersion':version,'manifestHash':version,'documents':{p:v for p,v in documents.items()if paths is None or p in paths},'hashes':{p:h for p,h in hashes.items()if paths is None or p in paths}}
 def entry(self,entry_id):
  require(isinstance(entry_id,str)and ID.fullmatch(entry_id),'KNOWLEDGE_ENTRY_ID_INVALID')
  snapshot=self.snapshot();path,value=find_entry(snapshot['documents'],entry_id)
  if value is None or value.get('status')=='deleted' or snapshot['documents'].get(category_path(value.get('categoryId','')),{}).get('status')=='deleted':return None
  return {**value,'filexSource':{'sourceVersion':snapshot['sourceVersion'],'contentHash':snapshot['hashes'][path],'path':path}}
 @staticmethod
 def _category(documents,category_id):
  category=documents.get(category_path(category_id))
  if category is None or category.get('status')=='deleted':return None
  entries=[v for p,v in sorted(documents.items())if '/entries/'in p and v.get('categoryId')==category_id and v.get('status')!='deleted']
  return {**category,'entryCount':len(entries),'entries':[{k:v[k]for k in ('id','type','title','purpose','triggers','authority','status','maxReadChars','version','updatedAt')if k in v}for v in entries]}
 def overview(self):
  documents=self.snapshot()['documents'];categories=[self._category(documents,value['id'])for path,value in sorted(documents.items())if path.endswith('/metadata.json')]
  return {**documents['overview.json'],'categories':[{k:v for k,v in c.items()if k!='entries'}for c in categories if c is not None]}
 def category(self,category_id):
  require(isinstance(category_id,str)and ID.fullmatch(category_id),'KNOWLEDGE_CATEGORY_ID_INVALID')
  return self._category(self.snapshot()['documents'],category_id)
 def laya_catalog(self):return self.compile_laya_catalog(self.snapshot())
 @staticmethod
 def compile_laya_catalog(snapshot):
  from .registry_contract import validate_active_registry
  import copy
  documents=snapshot['documents'];path,core=find_entry(documents,'laya-worker-registry')
  require(core is not None and core.get('status')=='active'and core.get('authority')=='authoritative' and documents.get(category_path(core.get('categoryId','')),{}).get('status')!='deleted','LAYA_KNOWLEDGE_NOT_CONFIRMED')
  registry=copy.deepcopy(validate_active_registry(core.get('data')));actions={a['id']:a for a in registry['actions']};guidance=[];sources=[]
  for p,entry in sorted(documents.items()):
   if '/entries/'not in p or documents.get(category_path(entry.get('categoryId','')),{}).get('status')=='deleted' or entry.get('status')!='active' or entry.get('authority')!='authoritative':continue
   sources.append({'entryId':entry['id'],'path':p,'entryVersion':entry.get('version'),'contentHash':snapshot['hashes'][p]})
   if entry['id']=='laya-worker-registry':continue
   data=entry.get('data',{});routing=data.get('routing')if isinstance(data,dict)else None
   if routing is not None:
    require(isinstance(routing,dict)and set(routing)=={'requestType','stageIds'},'LAYA_KNOWLEDGE_ROUTING_INVALID')
    name=routing['requestType'];ids=routing['stageIds']
    require(isinstance(name,str)and ID.fullmatch(name)and isinstance(ids,list)and ids and len(ids)==len(set(ids))and all(i in actions for i in ids),'LAYA_KNOWLEDGE_EXECUTABLE_STAGE_UNKNOWN')
    require(name not in registry['requestTypes'],'LAYA_KNOWLEDGE_ROUTING_COLLISION')
    registry['requestTypes'][name]={'stageIds':ids}
   guidance.append({'entryId':entry['id'],'title':entry.get('title',''),'purpose':entry.get('purpose',''),'content':entry.get('content',''),'keywords':entry.get('triggers',[]),'sections':data.get('sections',[])if isinstance(data,dict)else[],'routing':routing})
  registry['knowledgeGuidance']=guidance
  validate_active_registry(registry)
  return {'schemaVersion':'ngs.laya-knowledge-catalog.v1','sourceVersion':snapshot['sourceVersion'],'registry':registry,'sources':sources,'coreEntryVersion':core['version'],'coreRegistryHash':digest(core['data'])}
