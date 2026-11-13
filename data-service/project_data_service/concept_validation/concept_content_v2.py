"""Approved additive v2 contract. Legacy v1 remains delegated to original validator."""
import re
def identifier(v):
 if not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,180}',v) or v in ('__proto__','constructor','prototype'):raise ValueError('ID를 확인하세요.')
 return v
def strings(v,minimum,maximum,limit,error):
 if not isinstance(v,list) or not minimum<=len(v)<=maximum or any(not isinstance(s,str) or not s.strip() or len(s)>limit for s in v) or len(set(v))!=len(v):raise ValueError(error)
 return v

def validate(document,seed,legacy_validator=None):
 if not isinstance(document,dict) or document.get('schemaVersion') not in (1,2) or document.get('projectId')!='ProjectBS':raise ValueError('문서 버전과 프로젝트를 확인하세요.')
 if document['schemaVersion']==1 and legacy_validator is not None:return legacy_validator(document)
 if set(document)-{'schemaVersion','projectId','title','sections','hierarchy'}:raise ValueError('허용되지 않은 문서 속성입니다.')
 if not isinstance(document.get('title'),str) or not document['title'].strip() or len(document['title'])>120:raise ValueError('문서 제목을 확인하세요.')
 sections=document.get('sections');maximum=8 if document['schemaVersion']==1 else 100
 if not isinstance(sections,list) or not 8<=len(sections)<=maximum or [s.get('id') for s in sections[:8]]!=[s['id'] for s in seed['sections']]:raise ValueError('기존 필수 카테고리 ID와 순서를 보존하세요.')
 ids=[];item_ids={}
 for index,section in enumerate(sections):
  if not isinstance(section,dict) or set(section)-{'id','title','description','priority','fields','summary'} or not {'id','title','description','priority','fields'}<=set(section):raise ValueError('카테고리 규격을 확인하세요.')
  identifier(section['id']);ids.append(section['id'])
  for key,limit in [('title',200),('description',200)]:
   if not isinstance(section[key],str) or len(section[key])>limit or key=='title' and not section[key].strip():raise ValueError('카테고리 이름과 소개를 확인하세요.')
  fields=section['fields'];expected=seed['sections'][index]['fields'] if index<8 else [];max_items=len(expected) if document['schemaVersion']==1 else 128
  if not isinstance(fields,list) or not len(expected)<=len(fields)<=max_items or [f.get('id') for f in fields[:len(expected)]]!=[f['id'] for f in expected]:raise ValueError('기존 필수 세부 항목 ID와 순서를 보존하세요.')
  if index>=8 and (not fields or not isinstance(section.get('summary'),dict)):raise ValueError('새 카테고리는 완전한 요약과 하나 이상의 항목이 필요합니다.')
  names=[]
  for field_index,f in enumerate(fields):
   if not isinstance(f,dict) or set(f)-{'id','label','value','status','sources','priority','summary'} or not {'id','label','value','status','sources','priority'}<=set(f):raise ValueError('세부 항목 규격을 확인하세요.')
   identifier(f['id']);names.append(f['id']);strings([f['label']],1,1,100,'세부 항목 이름을 확인하세요.')
   if f['status'] not in ('confirmed','source','proposal','open') or not isinstance(f['value'],str) or len(f['value'])>12000 or f['status']!='open' and not f['value'].strip():raise ValueError('세부 항목 상태와 본문을 확인하세요.')
   strings(f['sources'],0,20,300,'세부 항목 출처를 확인하세요.')
   if f['status']=='source' and not f['sources']:raise ValueError('출처 기반 항목에는 출처가 필요합니다.')
   if field_index>=len(expected) and not isinstance(f.get('summary'),dict):raise ValueError('새 세부 항목은 키워드·한 문장 요약이 필요합니다.')
   if 'summary' in f:validate_summary(f['summary'])
  if len(set(names))!=len(names) or any(type(f['priority'])!=int for f in fields) or sorted(f['priority'] for f in fields)!=list(range(1,len(fields)+1)):raise ValueError('세부 항목 ID와 우선순위가 중복됩니다.')
  item_ids[section['id']]=set(names)
  if 'summary' in section:validate_summary(section['summary'])
 if len(set(ids))!=len(ids) or any(type(s['priority'])!=int for s in sections) or sorted(s['priority'] for s in sections)!=list(range(1,len(sections)+1)):raise ValueError('카테고리 ID와 우선순위가 중복됩니다.')
 h=document.get('hierarchy')
 if h is None:raise ValueError('상위 그룹 구조가 필요합니다.')
 if not isinstance(h,dict) or h.get('schemaVersion')!='npc-concept-groups.v1' or set(h)-{'schemaVersion','groups','archivedCategoryIds','archivedItems'} or 'groups' not in h:raise ValueError('상위 그룹 구조를 확인하세요.')
 archived=h.get('archivedCategoryIds',[])
 if not isinstance(archived,list) or len(set(archived))!=len(archived) or not set(archived)<=set(ids):raise ValueError('보관 카테고리 참조를 확인하세요.')
 covered=set(archived);group_ids=set();groups=h['groups']
 if not isinstance(groups,list) or not 1<=len(groups)<=100:raise ValueError('상위 그룹이 하나 이상 필요합니다.')
 for g in groups:
  if not isinstance(g,dict) or set(g)-{'id','title','description','categoryIds'} or not {'id','title','categoryIds'}<=set(g):raise ValueError('상위 그룹 규격을 확인하세요.')
  identifier(g['id']);strings([g['title']],1,1,100,'상위 그룹 이름을 확인하세요.')
  if g['id'] in group_ids or g['id'] in ids or not isinstance(g['categoryIds'],list):raise ValueError('상위 그룹 ID와 카테고리 참조를 확인하세요.')
  group_ids.add(g['id'])
  for c in g['categoryIds']:
   if c not in ids or c in covered:raise ValueError('카테고리는 한 그룹 또는 보관 목록에만 속해야 합니다.')
   covered.add(c)
 if covered!=set(ids):raise ValueError('모든 카테고리의 상위 그룹 또는 보관 위치가 필요합니다.')
 archive_items=h.get('archivedItems',[]);seen=set()
 if document['schemaVersion']==1 and archive_items:raise ValueError('세부 항목 보관에는 v2 계약이 필요합니다.')
 if not isinstance(archive_items,list) or len(archive_items)>4096:raise ValueError('보관 항목 참조를 확인하세요.')
 for item in archive_items:
  if not isinstance(item,dict) or set(item)!={'categoryId','itemId'}:raise ValueError('보관 항목 규격을 확인하세요.')
  pair=(item['categoryId'],item['itemId'])
  if pair in seen or item['itemId'] not in item_ids.get(item['categoryId'],set()):raise ValueError('보관 항목 참조가 없거나 중복됩니다.')
  seen.add(pair)
 return document

def validate_summary(value):
 if isinstance(value,str):
  if not value.strip() or len(value)>600:raise ValueError('요약을 확인하세요.')
  return
 if not isinstance(value,dict) or set(value)!={'schemaVersion','keywords','sentence'} or value['schemaVersion']!='npc-concept-summary.v1':raise ValueError('키워드와 한 문장 요약 규격을 확인하세요.')
 strings(value['keywords'],1,12,100,'요약 키워드를 확인하세요.')
 if not isinstance(value['sentence'],str) or not value['sentence'].strip() or len(value['sentence'])>600:raise ValueError('요약 문장을 확인하세요.')
