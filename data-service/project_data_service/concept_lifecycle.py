"""Formal, deterministic, recoverable concept file/folder lifecycle.
Required baseline categories/fields fail explicitly; no implicit cascade or purge.
"""
import copy,json,re
from .concept import ROOT,validate
from .concept_filex import split,join,PREFIX
from .request_primitives import digest
from .database import utc_now
from .rendering_rules import validate_document

def require(ok,reason):
 if not ok:raise ValueError(reason)
def priorities(document):
 for index,s in enumerate(document['sections']):
  s['priority']=index+1
  for i,f in enumerate(s['fields']):f['priority']=i+1

def remove_plan(document,scope):
 d=copy.deepcopy(document);cid=scope['categoryId'];iid=scope['itemId'];sections=d['sections'];h=d['hierarchy']
 section=next((s for s in sections if s['id']==cid),None);require(section is not None,'CONCEPT_REMOVE_CATEGORY_NOT_FOUND')
 baseline=json.loads((ROOT/'seed.json').read_text());base=next((s for s in baseline['sections']if s['id']==cid),None)
 if iid is None:
  require(base is None,'CONCEPT_REQUIRED_CATEGORY_REMOVAL_DENIED')
  group=next((g for g in h['groups']if cid in g['categoryIds']),None)
  info={'sectionIndex':sections.index(section),'groupId':group['id']if group else None,'groupPosition':group['categoryIds'].index(cid)if group else None,'categoryArchived':cid in h.get('archivedCategoryIds',[]),'archivedItems':[x for x in h.get('archivedItems',[])if x['categoryId']==cid]}
  sections.remove(section)
  for g in h['groups']:g['categoryIds']=[x for x in g['categoryIds']if x!=cid]
  if 'archivedCategoryIds'in h:h['archivedCategoryIds']=[x for x in h['archivedCategoryIds']if x!=cid]
  if 'archivedItems'in h:h['archivedItems']=[x for x in h['archivedItems']if x['categoryId']!=cid]
 else:
  require(base is None or iid not in {f['id']for f in base['fields']},'CONCEPT_REQUIRED_ITEM_REMOVAL_DENIED')
  field=next((f for f in section['fields']if f['id']==iid),None);require(field is not None,'CONCEPT_REMOVE_ITEM_NOT_FOUND')
  require(base is not None or len(section['fields'])>1,'CONCEPT_CATEGORY_LAST_ITEM_REMOVAL_DENIED')
  tomb={'categoryId':cid,'itemId':iid};info={'fieldIndex':section['fields'].index(field),'itemArchived':tomb in h.get('archivedItems',[])}
  section['fields'].remove(field)
  if 'archivedItems'in h:h['archivedItems']=[x for x in h['archivedItems']if x!=tomb]
 priorities(d);validate(d);validate_document(d);return d,info

def validate_request(p):
 require(isinstance(p,dict)and set(p)=={'schemaVersion','projectId','requestId','action','scope','expectedRevision','documentHash','backupId'},'CONCEPT_LIFECYCLE_REQUEST_INVALID')
 require(p['schemaVersion']=='ngs.concept-lifecycle.v1'and p['projectId']=='binari','CONCEPT_LIFECYCLE_SCHEMA_INVALID')
 require(isinstance(p['requestId'],str)and re.fullmatch(r'[A-Za-z0-9._:-]{8,180}',p['requestId']),'CONCEPT_LIFECYCLE_REQUEST_ID')
 require(p['action']in ('item.remove','category.remove'),'MASTER_RECOVERY_FEATURE_REMOVED')
 scope=p['scope'];require(isinstance(scope,dict)and set(scope)=={'categoryId','itemId'},'CONCEPT_LIFECYCLE_SCOPE')
 for v in scope.values():require(v is None or isinstance(v,str)and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',v),'CONCEPT_LIFECYCLE_SCOPE_ID')
 require(scope['categoryId']is not None and (scope['itemId']is None)==p['action'].startswith('category.'),'CONCEPT_LIFECYCLE_SCOPE_ACTION')
 restore=p['action'].endswith('restore-removed')
 require((isinstance(p['backupId'],str)and re.fullmatch('[a-f0-9]{64}',p['backupId']))if restore else p['backupId']is None,'CONCEPT_LIFECYCLE_BACKUP_ID')
 require(type(p['expectedRevision'])is int and p['expectedRevision']>0 and isinstance(p['documentHash'],str)and re.fullmatch('[a-f0-9]{64}',p['documentHash']),'CONCEPT_LIFECYCLE_BASIS')
 return p

def result(source,p,receipt):
 require(receipt['inputHash']==digest(p)and receipt['status']=='committed','CONCEPT_LIFECYCLE_RECEIPT_BINDING')
 snap=source.snapshot();require(all(snap['hashes'].get(k.removeprefix(PREFIX))==v for k,v in receipt['hashes'].items()),'CONCEPT_LIFECYCLE_RESULT_SUPERSEDED')
 require(all(k not in snap['documents']for k in receipt.get('removedPaths',[])),'CONCEPT_LIFECYCLE_REMOVAL_UNVERIFIED')
 current=join(snap['documents'])
 return {'requestId':p['requestId'],'inputHash':digest(p),'revision':current['revision'],'documentHash':digest(current['document']),'sourceVersion':snap['sourceVersion'],'backupId':receipt['backupId'],'recoverable':True,'mode':receipt['mode'],'verified':True,'scope':receipt['scope']}

def execute(source,p,*,recover_only=False):
 p=validate_request(p);key=p['requestId'];h=digest(p)
 with source.factory()as t:prior=t.receipt(key)
 if prior is not None:return result(source,p,prior)
 if recover_only:return None
 with source.factory()as t:require(t.capabilities().get('conceptRecoverableLifecycleV1')is True,'CONCEPT_FILEX_LIFECYCLE_UNAVAILABLE')
 snap=source.snapshot();current=join(snap['documents']);require(current['revision']==p['expectedRevision']and digest(current['document'])==p['documentHash'],'CONCEPT_LIFECYCLE_SOURCE_CONFLICT')
 document,info=remove_plan(current['document'],p['scope'])
 envelope={'revision':current['revision']+1,'savedAt':utc_now(),'note':'정식 마스터 파일 '+p['action']+' '+key,'document':document};after=split(envelope);before=snap['documents']
 files=[{'path':PREFIX+rel,'body':body,'expectedHash':snap['hashes'].get(rel)}for rel,body in after.items()if before.get(rel)!=body]
 body={'requestId':key,'inputHash':h,'scope':p['scope'],'expectedSourceVersion':snap['sourceVersion'],'files':files,'readSet':{PREFIX+rel:snap['hashes'].get(rel)for rel in set(before)|set(after)},'restoreInfo':info,'backupId':p['backupId']}
 with source.factory()as t:receipt=t.call('POST','/v1/concept/remove-scope',body)
 return result(source,p,receipt)
