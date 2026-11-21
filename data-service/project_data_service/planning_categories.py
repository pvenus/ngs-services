"""Registered planning-worker preset: required common sources + exact requested target.
Common source prose is preserved, not summarized or pruned by this adapter.
"""
import copy,json
from pathlib import Path
from .filex_read import canonical,digest,require
COMMON='common.npc-planning';TARGET='unique.npc-category:'
NATIVE_FIELDS={'kind':'인물 분류','role':'역할','summary':'핵심 요약','publicFace':'처음 보이는 모습','actualTruth':'실제 사실 · 숨겨진 정보','desire':'핵심 욕구','fear':'두려움 · 회피','values':'가치와 충돌 우선순위','belief':'세계를 해석하는 방식','behavior':'행동 판단 규칙','voice':'말투와 대화 규칙','arc':'변화 가능성과 유지할 원칙','relationships':'인물 관계와 갈등','stateKeys':'선택에 따라 이어질 상태','openQuestions':'미결정 · 정합성 검토','sourceRefs':'근거 문서','numericProfile':'수치 · 관계 · 판단 기준','traits':'기본 성향'}
RULE_IDS=('npc-planning.rules.v1','npc-planning.workflow.v1')
def execution_contract(id):
 """Read the versioned release configuration, never user knowledge or a database."""
 path=Path(__file__).resolve().parents[2]/'catalog-service/catalog_service/execution_contracts.json'
 document=json.loads(path.read_text())
 require(document.get('schemaVersion')=='ngs.execution-contracts.v1','WORKER_CONTEXT_CONTRACT_SCHEMA')
 matches=[row for row in document['contracts'] if row.get('id')==id]
 require(len(matches)==1,'WORKER_CONTEXT_RULES_UNAVAILABLE')
 return copy.deepcopy(matches[0])

def attach(snapshot,story,contracts=execution_contract):
 c=snapshot['_character'];concept=snapshot['_concept'];vector=snapshot['catalog']['sourceRevisionVector']
 require(story.get('revision')==vector['story'],'WORKER_CONTEXT_STORY_REVISION')
 doc=story['document'];ids=c.get('chapters',[]);require(isinstance(ids,list)and all(isinstance(x,str)for x in ids),'WORKER_CONTEXT_STORY_LINKS')
 chapters=doc.get('chapters');require(isinstance(chapters,list),'WORKER_CONTEXT_STORY_SHAPE');linked=[x for x in chapters if x.get('id')in ids]
 require(set(ids)=={x.get('id')for x in linked},'WORKER_CONTEXT_STORY_MISSING')
 common,refs=common_value(concept,doc,linked,contracts)
 common['npcCommon']={'identity':{k:copy.deepcopy(c[k])for k in ('name','occupation','role','kind')if k in c},'planningCategories':[copy.deepcopy(row)for row in c.get('planningCategories',[])if row.get('planningRole')!='custom' and row.get('name')in ('인물 개요','포트레이트용 아트') and row.get('lifecycleState')!='deleted' and isinstance(row.get('content'),str)and row['content'].strip()]}
 refs.extend({'id':row['id'],'revision':snapshot['catalog']['npcRevision'],'contentHash':digest(row)}for row in common['npcCommon']['planningCategories'])
 values={}
 def add(id,title,value):values[id]={'id':id,'title':title,'contentHash':digest(value),'value':copy.deepcopy(value)}
 add(COMMON,'공용 NPC 기획 기준',common)
 for field,name in NATIVE_FIELDS.items():
  if field not in c:continue
  content='\n'.join(c[field])if field=='sourceRefs'else canonical(c[field]).decode()if field in ('numericProfile','traits')else c[field]
  add(TARGET+'field:'+field,name,{'name':name,'content':content,**({'sourceFormat':'json'}if field in ('numericProfile','traits')else{})})
 for category in c.get('planningCategories',[]):
  if category.get('lifecycleState')=='deleted':continue
  add(TARGET+category['id'],category['name'],{k:category[k]for k in ('name','content','sourceFormat')if k in category})
 snapshot['values'].update(values);snapshot['catalog']['categories']=[{k:v[k]for k in ('id','title','contentHash')}for v in values.values()]
 snapshot['catalog']['registryRefs']=refs
 return snapshot

def common_value(concept,story_document,linked,contracts=execution_contract):
 require(callable(contracts),'WORKER_CONTEXT_CONTRACT_READER_REQUIRED');rules=[];refs=[]
 for id in RULE_IDS:
  value=copy.deepcopy(contracts(id));require(isinstance(value,dict),'WORKER_CONTEXT_RULES_UNAVAILABLE')
  require(value.get('id')==id and type(value.get('version'))is int and value['version']>0 and isinstance(value.get('content'),dict),'WORKER_CONTEXT_RULES_BINDING')
  rules.append(value);refs.append({'id':id,'revision':value['version'],'contentHash':digest(value),'source':'execution-contract'})
 return {'concept':copy.deepcopy(concept['document']),'story':{**copy.deepcopy(story_document),'chapters':copy.deepcopy(linked)},'rules':rules},refs
