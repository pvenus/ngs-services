"""Server-only publication of a formally selected or confirmed concept operation.
No physical delete, broad PUT, or authoring is available through this boundary.
"""
import copy,re
from .request_primitives import digest
from .concept import validate

def require(ok, message):
 if not ok:raise ValueError(message)

def validate_change(before,after,action,scope):
 """Reconstruct the only permitted transformation and compare the whole document."""
 out=copy.deepcopy(before);cid=scope.get('categoryId');iid=scope.get('itemId');gid=scope.get('groupId')
 old=next((s for s in out['sections']if s['id']==cid),None)
 new=next((s for s in after['sections']if s['id']==cid),None)
 h=out.get('hierarchy',{})
 if action=='category.add':
  added=[s for s in after['sections']if s['id']not in {x['id']for x in out['sections']}]
  require(len(added)==1,'CONCEPT_ADD_CATEGORY_SCOPE');added=added[0]
  group=next((g for g in h.get('groups',[])if g['id']==gid),None)
  require(group is not None,'CONCEPT_GROUP_NOT_FOUND')
  require(not any(s['title'].casefold()==added['title'].casefold()for s in out['sections']if s['id']not in h.get('archivedCategoryIds',[])),'CONCEPT_DUPLICATE_CATEGORY')
  out['sections'].append(copy.deepcopy(added));group['categoryIds'].append(added['id']);out['schemaVersion']=2
 elif action in ('item.add','item.modify','category.summary.modify'):
  require(old is not None and new is not None and cid not in h.get('archivedCategoryIds',[]),'CONCEPT_CATEGORY_NOT_FOUND')
  if action=='item.add':
   added=[f for f in new['fields']if f['id']not in {x['id']for x in old['fields']}]
   require(len(added)==1,'CONCEPT_ADD_ITEM_SCOPE')
   require(not any(f['label'].casefold()==added[0]['label'].casefold()for f in old['fields']),'CONCEPT_DUPLICATE_ITEM')
   old['fields'].append(copy.deepcopy(added[0]));out['schemaVersion']=2
  elif action=='category.summary.modify':old['summary']=copy.deepcopy(new.get('summary'))
  else:
   require({'categoryId':cid,'itemId':iid}not in h.get('archivedItems',[]),'CONCEPT_TARGET_ARCHIVED')
   f=next((f for f in old['fields']if f['id']==iid),None);nf=next((f for f in new['fields']if f['id']==iid),None)
   require(f is not None and nf is not None,'CONCEPT_ITEM_NOT_FOUND')
   # Identity and ordering are not editable content.
   require(f['id']==nf['id'] and f.get('priority')==nf.get('priority'),'CONCEPT_ITEM_IDENTITY_CHANGED')
   f.clear();f.update(copy.deepcopy(nf))
 elif action in ('item.archive','item.restore'):
  require(old is not None and any(f['id']==iid for f in old['fields']),'CONCEPT_ITEM_NOT_FOUND')
  tomb={'categoryId':cid,'itemId':iid};items=h.setdefault('archivedItems',[])
  if action=='item.archive':
   if tomb not in items:items.append(tomb)
  else:h['archivedItems']=[x for x in items if x!=tomb]
  out['schemaVersion']=2
 elif action in ('category.archive','category.restore'):
  require(old is not None,'CONCEPT_CATEGORY_NOT_FOUND');arch=h.setdefault('archivedCategoryIds',[])
  memberships=[g for g in h['groups']if cid in g['categoryIds']]
  if action=='category.archive':
   require(cid not in arch and len(memberships)==1,'CONCEPT_ARCHIVE_MEMBERSHIP')
   memberships[0]['categoryIds'].remove(cid);arch.append(cid)
  else:
   require(cid in arch and not memberships,'CONCEPT_RESTORE_MEMBERSHIP')
   group=next((g for g in h['groups']if g['id']==gid),None);pos=scope.get('categoryPosition')
   require(group is not None and type(pos)is int and 0<=pos<=len(group['categoryIds']),'CONCEPT_RESTORE_POSITION')
   require(not any(s['id']!=cid and s['id']not in arch and s['title'].casefold()==old['title'].casefold()for s in out['sections']),'CONCEPT_DUPLICATE_CATEGORY')
   group['categoryIds'].insert(pos,cid);arch.remove(cid)
  out['schemaVersion']=2
 else:raise ValueError('CONCEPT_PUBLICATION_ACTION_INVALID')
 require(out==after,'CONCEPT_PUBLICATION_OUT_OF_SCOPE')

def validate_publication(p):
 require(isinstance(p,dict) and set(p)=={'schemaVersion','requestId','operationHash','action','scope','expectedRevision','documentHash','document','evidence'},'CONCEPT_PUBLICATION_INVALID')
 require(p['schemaVersion']=='ngs.concept-publication.v1' and isinstance(p['requestId'],str) and re.fullmatch(r'[A-Za-z0-9._:-]{8,180}',p['requestId']),'CONCEPT_PUBLICATION_ID_INVALID')
 require(type(p['expectedRevision'])is int and p['expectedRevision']>0 and isinstance(p['documentHash'],str) and re.fullmatch('[a-f0-9]{64}',p['documentHash']),'CONCEPT_PUBLICATION_BASIS_INVALID')
 require(isinstance(p['scope'],dict) and set(p['scope'])=={'categoryId','itemId','groupId','categoryPosition'},'CONCEPT_PUBLICATION_SCOPE_INVALID')
 ev=p['evidence'];require(isinstance(ev,dict),'CONCEPT_PUBLICATION_EVIDENCE_INVALID')
 if p['action'] in ('category.add','item.add','item.modify','category.summary.modify'):
  require(set(ev)=={'kind','proposalHash','selectedId'} and ev['kind']=='selected' and all(isinstance(ev[k],str) and ev[k]for k in ('proposalHash','selectedId')),'CONCEPT_SELECTION_REQUIRED')
  key=digest({'requestId':p['requestId'],'proposalHash':ev['proposalHash'],'selectedId':ev['selectedId']})
 else:
  require(set(ev)=={'kind','requestHash','confirmed'} and ev['kind']=='deterministic' and ev['confirmed']is True and isinstance(ev['requestHash'],str),'CONCEPT_CONFIRMATION_REQUIRED')
  key=digest({'requestId':p['requestId'],'requestHash':ev['requestHash'],'action':p['action']})
 require(key==p['operationHash'],'CONCEPT_OPERATION_BINDING_INVALID');validate(copy.deepcopy(p['document']))
 return p

def publish(source,p):
 p=validate_publication(p)
 require(p['action'] not in ('item.restore','category.restore'),'MASTER_RECOVERY_FEATURE_REMOVED')
 h=digest(p);key='publication.'+p['operationHash']
 prior=source.recover(key,h)
 if prior:return receipt(prior,p)
 current=source.current()
 require(current['revision']==p['expectedRevision'] and digest(current['document'])==p['documentHash'],'CONCEPT_SOURCE_CONFLICT')
 validate_change(current['document'],p['document'],p['action'],p['scope'])
 result=source.apply({'requestId':key,'expectedRevision':p['expectedRevision'],'documentHash':p['documentHash']},h,p['document'],{},allow_add=True)
 return receipt(result,p)

def receipt(result,p):
 return {'commitId':'concept.publication.'+str(result['revision'])+'.'+p['operationHash'],'revision':result['revision'],'documentHash':result['documentHash'],'operationHash':p['operationHash']}

def recover_publication(source,p):
 p=validate_publication(p);result=source.recover('publication.'+p['operationHash'],digest(p))
 return receipt(result,p) if result else None
