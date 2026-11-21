"""Version-pinned worker context: worker declares categories, PDS returns only those.
The PDS reads verified published Filex bytes; no worker filesystem/Filex access,
no history or candidate endpoint, no source writes and no model execution.
"""
import copy, hashlib, json, re, threading
from .filex_read import FilexRead, PREFIX, canonical, digest, require
from .planning_categories import execution_contract

CATEGORY_TITLES={'common.portrait-rendering':'공용 포트레이트 렌더링·문화 기준','unique.npc-identity':'인물 식별','unique.npc-design':'인물 고유 디자인','unique.npc-performance':'인물 역할·행동 연출'}
class ContextConflict(ValueError):pass
class ContextMissing(ValueError):pass
class PlanningItems:
 def __init__(self,transport_factory=FilexRead,contract_reader=execution_contract,npc_reader=None,concept_reader=None):self.factory=transport_factory;self.contracts=contract_reader;self.npc_reader=npc_reader;self.concept_reader=concept_reader;self.cache={};self.documents={};self.lock=threading.RLock()
 def _snapshot(self,transport,project,npc,expected=None):
  require(project=='binari' and isinstance(npc,str)and re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]*',npc),'WORKER_CONTEXT_SCOPE')
  raw=transport.get(PREFIX+'current.json');pointer=json.loads(raw);version=pointer.get('version')
  require(pointer.get('schemaVersion')=='ngs-published-files-current.v1' and pointer.get('projectId')==project and isinstance(version,str)and re.fullmatch('[a-f0-9]{64}',version),'WORKER_CONTEXT_POINTER')
  published_version=version
  current_npcs=self.npc_reader()if self.npc_reader else None
  current_concept=self.concept_reader()if self.concept_reader else None
  if current_npcs is not None:version=digest([published_version,current_npcs['revision'],digest(current_npcs['document'])])
  if current_concept is not None:version=digest([version,current_concept['revision'],digest(current_concept['document'])])
  if expected is not None and version!=expected:raise ContextConflict('WORKER_CONTEXT_SOURCE_CHANGED')
  cached=self.cache.get((version,npc))
  if cached:
   require(cached['manifestHash']==pointer.get('manifestHash'),'WORKER_CONTEXT_POINTER_HASH')
   return raw,copy.deepcopy(cached)
  mraw=transport.get(PREFIX+'versions/'+published_version+'/manifest.json');m=json.loads(mraw)
  require(hashlib.sha256(mraw).hexdigest()==pointer.get('manifestHash')and m.get('schemaVersion')=='ngs-published-files.v1'and m.get('projectId')==project and m.get('version')==published_version and digest({k:v for k,v in m.items()if k!='version'})==published_version,'WORKER_CONTEXT_MANIFEST')
  def document(resource):
   entries=[e for e in m['files']if e.get('role')=='document'and e.get('resourceId')==resource]
   require(len(entries)==1,'WORKER_CONTEXT_DOCUMENT');e=entries[0];path=e['path']
   require(path=='documents/'+resource+'.json'and type(e['size'])is int and e['size']>=0,'WORKER_CONTEXT_DOCUMENT_PATH')
   data=transport.get(PREFIX+'versions/'+published_version+'/'+path,limit=e['size'])
   require(len(data)==e['size']and hashlib.sha256(data).hexdigest()==e['sha256'],'WORKER_CONTEXT_DOCUMENT_HASH');return json.loads(data)
  npcs,concept=current_npcs if current_npcs is not None else document('npcs'),current_concept if current_concept is not None else document('concept');vector=copy.deepcopy(m['source']['sourceRevisionVector'])
  if current_npcs is not None:vector['npcs']=npcs['revision']
  if current_concept is not None:vector['concept']=concept['revision'];vector['concept.currentDocumentHash']=digest(concept['document'])
  require(npcs.get('revision')==vector['npcs'] and concept.get('revision')==vector['concept'],'WORKER_CONTEXT_REVISION')
  matches=[n for n in npcs['document']['characters']if n.get('id')==npc]
  if not matches:raise ContextMissing('WORKER_CONTEXT_NPC_MISSING')
  require(len(matches)==1,'WORKER_CONTEXT_NPC_DUPLICATE');character=matches[0]
  values={};metadata=[]
  def add(id,value):
   h=digest(value);meta={'id':id,'title':CATEGORY_TITLES[id],'contentHash':h}
   metadata.append(meta);values[id]={**meta,'value':copy.deepcopy(value)}
  rendering=rendering_value(concept)
  add('common.portrait-rendering',rendering)
  require(all(k in character for k in ('name','kind')),'WORKER_CONTEXT_IDENTITY_MISSING')
  add('unique.npc-identity',{k:character[k]for k in ('name','kind','role','occupation')if k in character})
  preset=character.get('planningPreset',{})
  if preset.get('id')=='npc.standard'and preset.get('version')in ('2','3'):
   visible=preset.get('visibleCategoryIds');require(isinstance(visible,list)and len(set(visible))==len(visible),'WORKER_CONTEXT_PRESET')
   rows=character.get('planningCategories',[]);design=[next((c for c in rows if c.get('id')==id),None)for id in visible]
   require(all(isinstance(c,dict)for c in design),'WORKER_CONTEXT_PRESET')
  else:design=[c for c in character.get('planningCategories',[])if c.get('planningRole')!='custom' and c.get('name')in ('개별 아트 연출','캐릭터 디자인 특징')]
  design=[c for c in design if c.get('lifecycleState')!='deleted']
  require(len({c['name']for c in design})==len(design),'WORKER_CONTEXT_DESIGN_DUPLICATE')
  # Do not synthesize or approve missing character design. Worker can report it.
  add('unique.npc-design',{'planningCategories':design})
  add('unique.npc-performance',{k:character[k]for k in ('role','publicFace','behavior')if k in character})
  ref={'npcRevision':npcs['revision'],'characterHash':digest(character),'activePortraitId':character.get('activePortraitId')}
  base={'schemaVersion':'pds-worker-context-catalog.v2','projectId':project,'npcId':npc,**ref,'characterRef':ref,'masterRevision':concept['revision'],'masterDocumentHash':digest(concept['document']),'masterSource':'pds-current-authority'if self.concept_reader else'legacy-published','sourceVersion':version,'sourceRevisionVector':vector,'categories':metadata}
  value={'catalog':base,'values':values,'manifestHash':pointer['manifestHash'],'_character':character,'_concept':concept,'_manifest':m}
  if len(self.cache)>=8:self.cache.pop(next(iter(self.cache)))
  self.cache[(version,npc)]=copy.deepcopy(value);return raw,value
 def _planning(self,transport,snapshot):
  from .planning_categories import attach
  m=snapshot['_manifest'];key=(m['version'],'story');story=self.documents.get(key)
  if story is None:
   entries=[e for e in m['files']if e.get('role')=='document'and e.get('resourceId')=='story'];require(len(entries)==1,'WORKER_CONTEXT_STORY_DOCUMENT');e=entries[0]
   require(e['path']=='documents/story.json','WORKER_CONTEXT_STORY_PATH')
   raw=transport.get(PREFIX+'versions/'+m['version']+'/'+e['path'],limit=e['size'])
   require(len(raw)==e['size']and hashlib.sha256(raw).hexdigest()==e['sha256'],'WORKER_CONTEXT_STORY_HASH');story=json.loads(raw)
   if len(self.documents)>=8:self.documents.clear()
   self.documents[key]=story
  return attach(snapshot,story,self.contracts)
 def create_read(self,payload):
  from .planning_categories import common_value
  ids=['common.npc-planning','common.portrait-rendering']
  require(isinstance(payload,dict)and set(payload)=={'projectId','actId','chapterId','expectedRevisions','categories'} and payload['projectId']=='binari'and payload['categories']==ids,'WORKER_CREATE_READ_SCHEMA')
  expected=payload['expectedRevisions'];require(isinstance(expected,dict)and set(expected)=={'npcs','concept','story'}and all(type(v)is int and v>=0 for v in expected.values()),'WORKER_CREATE_REVISIONS')
  require(all(isinstance(payload[k],str)and payload[k]for k in ('actId','chapterId')),'WORKER_CREATE_STORY_SCOPE')
  with self.lock,self.factory()as transport:
   raw=transport.get(PREFIX+'current.json');p=json.loads(raw);version=p.get('version')
   require(p.get('schemaVersion')=='ngs-published-files-current.v1'and p.get('projectId')=='binari'and isinstance(version,str)and re.fullmatch('[a-f0-9]{64}',version),'WORKER_CONTEXT_POINTER')
   mraw=transport.get(PREFIX+'versions/'+version+'/manifest.json');m=json.loads(mraw)
   require(hashlib.sha256(mraw).hexdigest()==p.get('manifestHash')and m.get('schemaVersion')=='ngs-published-files.v1'and m.get('projectId')=='binari'and m.get('version')==version and digest({k:v for k,v in m.items()if k!='version'})==version,'WORKER_CONTEXT_MANIFEST')
   published_version=version
   vector=copy.deepcopy(m['source']['sourceRevisionVector'])
   current_concept=self.concept_reader()if self.concept_reader else None
   if current_concept is not None:vector['concept']=current_concept['revision'];vector['concept.currentDocumentHash']=digest(current_concept['document']);version=digest([version,current_concept['revision'],digest(current_concept['document'])])
   if any(vector.get(k)!=v for k,v in expected.items()):raise ContextConflict('WORKER_CREATE_SOURCE_CHANGED')
   def document(id):
    entries=[e for e in m['files']if e.get('role')=='document'and e.get('resourceId')==id];require(len(entries)==1,'WORKER_CONTEXT_DOCUMENT');e=entries[0]
    require(e['path']=='documents/'+id+'.json'and type(e['size'])is int and e['size']>=0,'WORKER_CONTEXT_DOCUMENT_PATH')
    data=transport.get(PREFIX+'versions/'+published_version+'/'+e['path'],limit=e['size']);require(len(data)==e['size']and hashlib.sha256(data).hexdigest()==e['sha256'],'WORKER_CONTEXT_DOCUMENT_HASH');value=json.loads(data)
    require(value.get('revision')==expected[id],'WORKER_CONTEXT_REVISION');return value
   concept,story=current_concept if current_concept is not None else document('concept'),document('story');doc=story['document'];acts=[doc,*doc.get('storylines',[])];matches=[a for a in acts if a.get('storyId')==payload['actId']and any(c.get('id')==payload['chapterId']for c in a.get('chapters',[]))]
   require(len(matches)==1,'WORKER_CREATE_STORY_SCOPE');act=matches[0];linked=[c for c in act['chapters']if c.get('id')==payload['chapterId']];require(len(linked)==1,'WORKER_CREATE_STORY_SCOPE')
   common,refs=common_value(concept,act,linked,self.contracts);rendering=rendering_value(concept)
   categories=[{'id':id,'contentHash':digest(value),'value':value}for id,value in zip(ids,(common,rendering))]
   if transport.get(PREFIX+'current.json')!=raw:raise ContextConflict('WORKER_CONTEXT_SOURCE_CHANGED')
   if self.concept_reader and self.concept_reader()!=current_concept:raise ContextConflict('WORKER_CONTEXT_MASTER_CHANGED')
   return {'schemaVersion':'pds-worker-create-context.v1','projectId':'binari','actId':payload['actId'],'chapterId':payload['chapterId'],'sourceVersion':version,'sourceRevisionVector':vector,'registryRefs':refs,'categories':categories,'trace':{'source':'pds-filex','selectedCategoryIds':ids,'historyIncluded':False,'candidateDataIncluded':False}}
 def catalog(self,project,npc,worker='npc-portrait'):
  with self.lock,self.factory()as transport:
   require(worker in ('npc-portrait','npc-planning','npc-animation'),'WORKER_CONTEXT_WORKER_UNSUPPORTED')
   raw,s=self._snapshot(transport,project,npc)
   if worker=='npc-planning':s=self._planning(transport,s)
   if worker=='npc-animation':s=animation_catalog(s)
   if transport.get(PREFIX+'current.json')!=raw:raise ContextConflict('WORKER_CONTEXT_SOURCE_CHANGED')
   self._check_current(s)
   return s['catalog']
 def read(self,payload):
  animation=isinstance(payload,dict) and payload.get('worker')=='npc-animation'
  if animation:
   require(set(payload)=={'projectId','npcId','npcRevision','sourceVersion','categories','posePreset','referenceType','worker'},'WORKER_CONTEXT_READ_SCHEMA')
   require(payload['posePreset'] in ('combat','idle') and payload['referenceType'] in ('pixel','portrait') and payload['categories']==ANIMATION_CATEGORIES,'WORKER_CONTEXT_ANIMATION_SELECTION')
  else:require(isinstance(payload,dict)and set(payload) in ({'projectId','npcId','npcRevision','sourceVersion','categories'},{'projectId','npcId','npcRevision','sourceVersion','categories','posePreset'}),'WORKER_CONTEXT_READ_SCHEMA')
  pose=payload.get('posePreset')
  if pose is not None and not animation:require(pose in ('representative','combat','idle') and payload['categories']==['common.portrait-rendering','unique.npc-identity','unique.npc-design'],'WORKER_CONTEXT_POSE_PRESET')
  requested=payload['categories'];require(isinstance(requested,list)and bool(requested),'WORKER_CONTEXT_SELECTION_REQUIRED')
  planning=any(isinstance(x,str)and(x=='common.npc-planning'or x.startswith('unique.npc-category:'))for x in requested)
  if planning:
   require('common.npc-planning'in requested and all(isinstance(x,str)and(x=='common.npc-planning'or x.startswith('unique.npc-category:'))for x in requested),'WORKER_CONTEXT_PLANNING_PRESET')
   require(len(requested)<=4,'WORKER_CONTEXT_EXACT_TARGET_REQUIRED')
  elif not animation:require(all(isinstance(x,str)and x in CATEGORY_TITLES for x in requested),'WORKER_CONTEXT_CATEGORY_UNSUPPORTED')
  require(len(set(requested))==len(requested),'WORKER_CONTEXT_SELECTION_DUPLICATE')
  with self.lock,self.factory()as transport:
   raw,s=self._snapshot(transport,payload['projectId'],payload['npcId'],payload['sourceVersion'])
   if planning:
    s=self._planning(transport,s)
    validate_planning_selection(requested,s['_character'])
   if animation:s=animation_catalog(s)
   c=s['catalog']
   if type(payload['npcRevision'])is not int or payload['npcRevision']!=c['npcRevision']:raise ContextConflict('WORKER_CONTEXT_NPC_CHANGED')
   if any(id not in s['values']for id in requested):raise ContextMissing('WORKER_CONTEXT_CATEGORY_MISSING')
   categories=[copy.deepcopy(s['values'][id])for id in requested]
   evidence={}
   if animation:
    selected=portrait_planning(s['_character'],pose)
    reference=s['_character'].get('portraitSets',{}).get(pose,{})
    asset=reference.get(payload['referenceType']+'AssetId')
    require(isinstance(asset,str) and re.fullmatch(r'[a-f0-9]{64}\.(png|jpg|webp)',asset),'WORKER_CONTEXT_ANIMATION_REFERENCE_MISSING')
    entry=next(c for c in categories if c['id']=='unique.npc-design')
    entry['value']={'planningCategories':selected[:1]};entry['contentHash']=digest(entry['value'])
    evidence={'purpose':'npc-animation','posePreset':pose,'referenceType':payload['referenceType'],'referenceAssetId':asset,'validatedPoseCategoryId':selected[1]['id'],'validatedPoseContentHash':digest(selected[1]),'poseBodyIncluded':False,'masterSelectedKeys':list(ANIMATION_STYLE_KEYS),'worldFieldsIncluded':False}
   elif pose is not None:
    selected=portrait_planning(s['_character'],pose)
    entry=next(c for c in categories if c['id']=='unique.npc-design')
    entry['value']={'planningCategories':selected};entry['contentHash']=digest(entry['value'])
   if transport.get(PREFIX+'current.json')!=raw:raise ContextConflict('WORKER_CONTEXT_SOURCE_CHANGED')
   self._check_current(s)
   return {**{k:v for k,v in c.items()if k!='categories'},'schemaVersion':'pds-worker-context-read.v2','categories':categories,'trace':{'source':'pds-filex','selectedCategoryIds':requested,'returnedCategories':len(categories),'historyIncluded':False,'candidateDataIncluded':False,**evidence}}

 def _check_current(self,snapshot):
  if self.concept_reader is not None:
   current=self.concept_reader();expected=snapshot['_concept']
   if current['revision']!=expected['revision'] or digest(current['document'])!=digest(expected['document']):raise ContextConflict('WORKER_CONTEXT_MASTER_CHANGED')
  if self.npc_reader is not None:
   current=self.npc_reader();c=snapshot['catalog']
   matches=[n for n in current['document']['characters']if n.get('id')==c['npcId']]
   if current['revision']!=c['npcRevision'] or len(matches)!=1 or digest(matches[0])!=c['characterHash']:raise ContextConflict('WORKER_CONTEXT_NPC_CHANGED')

def rendering_value(concept):
 sections=concept['document']['sections']
 expression=[f for section in sections if section['id']=='expression' for f in section['fields']]
 def registered_art(field):
  try:return json.loads(field.get('value','')).get('schemaVersion')=='ngs.character-art-direction.v2'
  except (ValueError,TypeError,AttributeError):return False
 from .rendering_rules import fields as rule_fields
 scoped=rule_fields(concept)
 master=[f for f in expression if f['id'] in ('visualStyle','visualProhibitions') or registered_art(f)]
 require(sum(registered_art(f)for f in master)<=1,'WORKER_CONTEXT_RENDERING_AMBIGUOUS')
 require({f['id']for f in master}>={'visualStyle','visualProhibitions'},'WORKER_CONTEXT_RENDERING_MISSING')
 world=[f for section in sections if section['id']=='world'for f in section['fields']if f['id']in ('eraCulture','prohibitions')]
 require({f['id']for f in world}=={'eraCulture','prohibitions'},'WORKER_CONTEXT_WORLD_MISSING')
 return {'masterFields':master+scoped,'worldFields':world}


PORTRAIT_POSE_TITLES={'representative':'대표 포즈 기획','combat':'전투 포즈 기획','idle':'아이들 포즈 기획'}
def portrait_planning(character,pose):
 require(pose in PORTRAIT_POSE_TITLES,'WORKER_CONTEXT_POSE_PRESET')
 from .planning_roles import select_portrait_planning
 try:return copy.deepcopy(select_portrait_planning(character,pose))
 except ValueError as error:raise ContextMissing("WORKER_CONTEXT_POSE_PLANNING_MISSING") from error

def validate_planning_selection(requested,character):
 """One target plus the two registered NPC common files, never arbitrary multi-target reads."""
 shared={'unique.npc-category:'+c['id']for c in character.get('planningCategories',[])if c.get('planningRole')!='custom' and c.get('name')in ('인물 개요','포트레이트용 아트') and c.get('lifecycleState')!='deleted'}
 targets=set(requested)-{'common.npc-planning'}-shared
 require(len(targets)<=1 and len(requested)<=4,'WORKER_CONTEXT_EXACT_TARGET_REQUIRED')


ANIMATION_CATEGORIES=['common.animation-rendering','unique.npc-identity','unique.npc-design']
ANIMATION_STYLE_KEYS=('styleBlock','lineHierarchy','palettePolicy','surfaceDetail')
def animation_catalog(snapshot):
 s=copy.deepcopy(snapshot)
 fields=[f for section in s['_concept']['document']['sections'] if section['id']=='expression' for f in section['fields'] if f['id']=='visualStyle']
 require(len(fields)==1,'WORKER_CONTEXT_ANIMATION_STYLE_MISSING')
 try:style=json.loads(fields[0]['value'])
 except (ValueError,TypeError):raise ValueError('WORKER_CONTEXT_ANIMATION_STYLE_SCHEMA')
 require(isinstance(style,dict) and style.get('schemaVersion')=='ngs.character-style-contract.v1','WORKER_CONTEXT_ANIMATION_STYLE_SCHEMA')
 require(all(isinstance(style.get(k),str) and style[k].strip() for k in ANIMATION_STYLE_KEYS),'WORKER_CONTEXT_ANIMATION_STYLE_INCOMPLETE')
 value={'schemaVersion':style['schemaVersion'],**{k:style[k] for k in ANIMATION_STYLE_KEYS}}
 from .rendering_rules import fields as rule_fields,selected,SCHEMA as RULE_SCHEMA
 registered=rule_fields(s['_concept'])
 if registered:value['renderingRules']={'schemaVersion':RULE_SCHEMA,'rules':selected(registered[0]['value'],'animation')}
 entry={'id':ANIMATION_CATEGORIES[0],'title':'애니메이션 공통 시각 스타일','contentHash':digest(value),'value':value}
 s['values'][entry['id']]=entry
 s['catalog']['categories']=[{k:v for k,v in s['values'][id].items() if k!='value'} for id in ANIMATION_CATEGORIES]
 return s
