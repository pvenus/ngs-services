"""Pure upper-group contract: no storage, requests, initialization or model calls."""
import copy,re,unicodedata,hashlib,json
class GroupError(ValueError):pass
def text(value,limit,empty=False):
 if not isinstance(value,str):raise GroupError('GROUP_TEXT_INVALID')
 value=unicodedata.normalize('NFC',value).strip()
 if (not empty and not value) or len(value.encode('utf-16-le'))//2>limit or re.search(r'[\x00-\x1f\x7f]',value):raise GroupError('GROUP_TEXT_INVALID')
 return value
def valid_id(value):
 return isinstance(value,str) and bool(re.fullmatch(r'[A-Za-z0-9._:-]{1,180}',value)) and value not in {'__proto__','constructor','prototype'}
def validate_hierarchy(value,category_ids):
 if not isinstance(value,dict) or set(value)-{'schemaVersion','groups','archivedCategoryIds','archivedItems'} or not {'schemaVersion','groups'}<=set(value) or value['schemaVersion']!='npc-concept-groups.v1' or not isinstance(value['groups'],list) or not 1<=len(value['groups'])<=100:raise GroupError('HIERARCHY_INVALID')
 if 'archivedItems' in value and (not isinstance(value['archivedItems'],list) or any(not isinstance(x,dict) or set(x)!={'categoryId','itemId'} or x['categoryId'] not in category_ids or not valid_id(x['itemId']) for x in value['archivedItems'])):raise GroupError('ITEM_ARCHIVE_INVALID')
 archived=value.get('archivedCategoryIds',[])
 if not isinstance(archived,list) or any(not isinstance(cid,str) or cid not in category_ids for cid in archived) or len(set(archived))!=len(archived):raise GroupError('CATEGORY_ARCHIVE_INVALID')
 seen=set();assigned=set()
 for g in value['groups']:
  if not isinstance(g,dict) or set(g)-{'id','title','description','categoryIds'} or not {'id','title','categoryIds'}<=set(g) or not valid_id(g['id']) or g['id'] in seen or g['id'] in category_ids or not isinstance(g['categoryIds'],list):raise GroupError('GROUP_INVALID')
  text(g['title'],100)
  if 'description' in g:text(g['description'],1000,True)
  seen.add(g['id'])
  for cid in g['categoryIds']:
   if not isinstance(cid,str) or cid not in category_ids or cid in assigned or cid in archived:raise GroupError('CATEGORY_ASSIGNMENT_INVALID')
   assigned.add(cid)
 if assigned|set(archived)!=set(category_ids):raise GroupError('CATEGORY_COVERAGE_REQUIRED')
 return copy.deepcopy(value)
def validate_operation(value):
 if not isinstance(value,dict) or value.get('schemaVersion')!='npc-concept-group-operation.v1':raise GroupError('GROUP_OPERATION_INVALID')
 action=value.get('action');allowed={'concept.group.add':{'schemaVersion','action','title','description','displayOrder'},'concept.group.update':{'schemaVersion','action','groupId','title','description','displayOrder'},'concept.group.delete':{'schemaVersion','action','groupId','moveToGroupId','categoryDispositions'}}
 if action not in allowed or set(value)-allowed[action]:raise GroupError('GROUP_OPERATION_INVALID')
 v=copy.deepcopy(value)
 if action!='concept.group.add' and not valid_id(v.get('groupId')):raise GroupError('GROUP_ID_INVALID')
 if action=='concept.group.add' and not {'title','displayOrder'}<=set(v):raise GroupError('GROUP_OPERATION_INVALID')
 if action=='concept.group.update' and not set(v)&{'title','description','displayOrder'}:raise GroupError('GROUP_OPERATION_EMPTY')
 if 'title' in v:v['title']=text(v['title'],100)
 if 'description' in v:v['description']=text(v['description'],1000,True)
 if 'displayOrder' in v and (type(v['displayOrder'])!=int or not 0<=v['displayOrder']<=99):raise GroupError('GROUP_ORDER_INVALID')
 if 'moveToGroupId' in v and not valid_id(v['moveToGroupId']):raise GroupError('GROUP_MOVE_TARGET_INVALID')
 if 'categoryDispositions' in v:
  ds=v['categoryDispositions']
  if action!='concept.group.delete' or 'moveToGroupId' in v or not isinstance(ds,list) or len(ds)>100:raise GroupError('GROUP_CATEGORY_DISPOSITION_INVALID')
  seen=set()
  for item in ds:
   if not isinstance(item,dict) or item.get('action') not in ('archive','move') or not valid_id(item.get('categoryId')):raise GroupError('GROUP_CATEGORY_DISPOSITION_INVALID')
   required={'categoryId','action'}|({'targetGroupId'} if item['action']=='move' else set())
   if set(item)!=required or item['categoryId'] in seen or item['action']=='move' and not valid_id(item.get('targetGroupId')):raise GroupError('GROUP_CATEGORY_DISPOSITION_INVALID')
   seen.add(item['categoryId'])
 return v
def result_group_id(operation,project_id,request_id):
 op=validate_operation(operation)
 if op['action']!='concept.group.add':return op['groupId']
 if project_id!='binari' or not valid_id(request_id):raise GroupError('GROUP_SCOPE_INVALID')
 body={'action':'concept.group.add','projectId':project_id,'requestId':request_id}
 return 'group.add.'+hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def apply_operation(document,operation,project_id,request_id):
 if project_id!='binari':raise GroupError('GROUP_SCOPE_INVALID')
 op=validate_operation(operation);d=copy.deepcopy(document)
 if 'hierarchy' not in d:raise GroupError('CANONICAL_HIERARCHY_NOT_INITIALIZED')
 ids=[s['id'] for s in d['sections']];h=validate_hierarchy(d['hierarchy'],ids);groups=h['groups'];identity=result_group_id(op,project_id,request_id);group=next((g for g in groups if g['id']==identity),None)
 if op['action']=='concept.group.add':
  if group:raise GroupError('GROUP_ID_EXISTS')
  if len(groups)>=100:raise GroupError('GROUP_LIMIT_REACHED')
  if op['displayOrder']>len(groups):raise GroupError('GROUP_ORDER_INVALID')
  group={'id':identity,'title':op['title'],'categoryIds':[]}
  if 'description' in op:group['description']=op['description']
  groups.insert(op['displayOrder'],group)
 elif op['action']=='concept.group.update':
  if group is None:raise GroupError('GROUP_NOT_FOUND')
  for k in ('title','description'):
   if k in op:group[k]=op[k]
  if 'displayOrder' in op:
   if op['displayOrder']>=len(groups):raise GroupError('GROUP_ORDER_INVALID')
   groups.remove(group);groups.insert(op['displayOrder'],group)
 else:
  if group is None:raise GroupError('GROUP_NOT_FOUND')
  if len(groups)==1:raise GroupError('LAST_GROUP_REQUIRED')
  if 'categoryDispositions' in op:
   dispositions={item['categoryId']:item for item in op['categoryDispositions']}
   if set(dispositions)!=set(group['categoryIds']):raise GroupError('GROUP_CATEGORY_DISPOSITION_MISMATCH')
   archived=list(h.get('archivedCategoryIds',[]))
   for cid in group['categoryIds']:
    item=dispositions[cid]
    if item['action']=='archive':archived.append(cid)
    else:
     target=next((g for g in groups if g['id']==item['targetGroupId'] and g['id']!=identity),None)
     if target is None:raise GroupError('GROUP_MOVE_TARGET_INVALID')
     target['categoryIds'].append(cid)
   if archived or 'archivedCategoryIds' in h:h['archivedCategoryIds']=archived
  else:
   # Retain old already-accepted whole-group move requests without changing intent.
   target=next((g for g in groups if g['id']==op.get('moveToGroupId') and g['id']!=identity),None)
   if 'moveToGroupId' in op and target is None or group['categoryIds'] and target is None:raise GroupError('GROUP_MOVE_TARGET_REQUIRED')
   if target:target['categoryIds'].extend(group['categoryIds'])
  groups.remove(group)
 titles=[text(g['title'],100) for g in groups]
 if len(set(titles))!=len(titles):raise GroupError('GROUP_TITLE_EXISTS')
 d['hierarchy']=validate_hierarchy(h,ids)
 return d,{'groupId':identity,'action':op['action'],'beforeHierarchy':copy.deepcopy(document['hierarchy']),'afterHierarchy':copy.deepcopy(h),'moveToGroupId':op.get('moveToGroupId'),'categoryDispositions':copy.deepcopy(op.get('categoryDispositions',[])),'archivedCategoryIds':[item['categoryId'] for item in op.get('categoryDispositions',[]) if item['action']=='archive'],'recovery':'restore a retained source revision using revision CAS'}
