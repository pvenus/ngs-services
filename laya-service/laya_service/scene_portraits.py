"""Read-only current-scene presence review and main-portrait pinning v1.

Names are candidates, never visibility decisions. A reviewed exact narrative
span with an on-screen action is required; uncertain language fails closed.
"""
import copy, hashlib, json, re

VERSION='npc-node-main-portrait-selection.v1'
def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(v):return hashlib.sha256(canonical(v).encode()).hexdigest()
def ensure(test,code):
 if not test:raise ValueError(code)
def binding(env):return {'revision':env['revision'],'documentHash':digest(env['document']),'envelopeHash':digest(env)}
def node_at(story,target):
 ensure(set(target)=={'projectId','actId','chapterId','episodeId','nodeId'} and target['projectId']=='binari','SCENE_PORTRAIT_TARGET_INVALID')
 ensure(story['document'].get('projectId')=='ProjectBS','SCENE_PORTRAIT_PROJECT_MISMATCH')
 def one(items,key,value):
  matches=[i for i in items if isinstance(i,dict) and i.get(key)==value];ensure(len(matches)==1,'SCENE_PORTRAIT_TARGET_AMBIGUOUS');return matches[0]
 root=one([story['document']]+story['document'].get('storylines',[]),'storyId',target['actId']);ch=one(root.get('chapters',[]),'id',target['chapterId']);ep=one(ch.get('episodes',[]),'id',target['episodeId']);return one(ep.get('playFlow',{}).get('nodes',[]),'id',target['nodeId'])
def occurrences(text,name):
 pattern=r'(?<![\w가-힣])'+re.escape(name)+r'(?=(?:께서는|에게는|께서|에게|한테|으로|부터|까지|처럼|보다|은|는|이|가|을|를|의|와|과|도|만|로)?(?:$|[^\w가-힣]))'
 return [(m.start(),m.end()) for m in re.finditer(pattern,text)]
def narrative_span(text,start,end):
 # Reject speech/quotation contents as evidence of visible presence.
 depth=False
 for i,c in enumerate(text):
  if c in '“”"':depth=not depth
  if i==start and depth:return False
  if start<=i<end and c in '“”"':return False
 return True

def select(story,npcs,target,review):
 node=node_at(story,target);body=node.get('body','');chars=npcs['document'].get('characters',[])
 ensure(npcs['document'].get('projectId')=='ProjectBS' and type(npcs.get('revision'))is int,'SCENE_PORTRAIT_NPC_SOURCE_INVALID')
 ensure(isinstance(review,dict) and set(review)=={'schemaVersion','storyBinding','npcBinding','nodeHash','decisions'} and review['schemaVersion']=='npc-node-presence-review.v1','SCENE_PORTRAIT_REVIEW_REQUIRED')
 ensure(review['storyBinding']==binding(story) and review['npcBinding']==binding(npcs) and review['nodeHash']==digest(node),'SCENE_PORTRAIT_SOURCE_CHANGED')
 names={}
 for c in chars:
  ensure(isinstance(c.get('id'),str),'SCENE_PORTRAIT_NPC_ID_INVALID');names.setdefault(c.get('name',''),[]).append(c['id'])
 byid={c['id']:c for c in chars};ensure(len(byid)==len(chars),'SCENE_PORTRAIT_DUPLICATE_NPC_ID')
 candidates={};blockers=[]
 branch='\n'.join(str(c.get('resultText','')) for c in node.get('choices',[]))
 occupied_body=[];occupied_branch=[]
 for name,ids in sorted(names.items(),key=lambda item:-len(item[0])):
  if not isinstance(name,str) or len(name)<2:continue
  spans=[(a,b) for a,b in occurrences(body,name) if not any(a<y and b>x for x,y in occupied_body)];branches=[(a,b) for a,b in occurrences(branch,name) if not any(a<y and b>x for x,y in occupied_branch)]
  occupied_body.extend(spans);occupied_branch.extend(branches)
  if spans or branches:
   if len(ids)!=1:blockers.append({'code':'AMBIGUOUS_CANONICAL_NAME','name':name,'candidateNpcIds':ids});continue
   candidates[ids[0]]={'nameToken':name,'bodySpans':spans,'branchOnly':not spans}
 # A node portrait association is an identity hint only. Relation-token matching
 # is limited to this explicitly bound NPC; it still needs a visible action.
 associated=node.get('portraitNpcId');mother=byid.get(associated,{})
 if mother.get('name','').endswith('어머니') and occurrences(body,'어머니'):
  ensure(not (set(names.get('어머니',[]))-{associated}),'SCENE_PORTRAIT_RELATION_NAME_AMBIGUOUS')
  candidates.setdefault(associated,{'nameToken':'어머니','bodySpans':occurrences(body,'어머니'),'branchOnly':False})
 ensure(isinstance(review['decisions'],list) and len(review['decisions'])<=32,'SCENE_PORTRAIT_REVIEW_INVALID')
 decisions={};selected=[];excluded=[];used_assets={}
 for d in review['decisions']:
  ensure(isinstance(d,dict) and set(d)=={'npcId','presence','quote','span','reason'} and d['npcId'] in byid and d['npcId'] not in decisions,'SCENE_PORTRAIT_ID_DUPLICATE_OR_UNKNOWN')
  ensure(d['presence'] in ('visible','mentioned','recollection','unselected','unresolved') and isinstance(d['reason'],str) and 10<=len(d['reason'])<=1500,'SCENE_PORTRAIT_REVIEW_INVALID')
  ensure(d['npcId'] in candidates,'SCENE_PORTRAIT_NO_NODE_IDENTITY_EVIDENCE');decisions[d['npcId']]=d;candidate=candidates[d['npcId']];text=branch if d['presence']=='unselected' else body
  ensure(isinstance(d['span'],list) and len(d['span'])==2 and all(type(v)is int for v in d['span']),'SCENE_PORTRAIT_SPAN_INVALID');start,end=d['span'];ensure(0<=start<end<=len(text) and text[start:end]==d['quote'] and candidate['nameToken'] in d['quote'],'SCENE_PORTRAIT_QUOTE_NOT_IN_SOURCE')
  if d['presence']=='unselected':ensure(candidate['branchOnly'],'SCENE_PORTRAIT_CURRENT_BODY_CANNOT_BE_BRANCH_ONLY')
  if d['presence']!='visible':
   if d['presence'] in ('mentioned','recollection') and not candidate['branchOnly'] and narrative_span(body,start,end) and re.match(re.escape(candidate['nameToken'])+r'(?:은|는|이|가|께서)\s',d['quote']) and re.search(r'손을\s*뻗|품에\s*안|물었|말했|바라보|서\s*있|앉|들어왔|걸어',d['quote']) and not re.search(r'회상|기억|떠올|상상|꿈\s*속|소문\s*속',d['quote']):raise ValueError('SCENE_PORTRAIT_VISIBLE_ACTION_CANNOT_BE_EXCLUDED')
   if d['presence']=='unresolved':blockers.append({'npcId':d['npcId'],'code':'VISIBLE_PRESENCE_UNRESOLVED'})
   excluded.append(copy.deepcopy(d));continue
  ensure(not candidate['branchOnly'] and narrative_span(body,start,end),'SCENE_PORTRAIT_NOT_CURRENT_NARRATIVE')
  ensure(not re.search(r'회상|기억\s*(?:속|하|했)|떠올|상상|꿈\s*속|소문\s*속|과거\s*장면|사진\s*속',d['quote']),'SCENE_PORTRAIT_RECOLLECTION_NOT_VISIBLE')
  # Conservative subject/action contract. Unsupported narrative needs a revised
  # explicit review; a mention/object/pronoun must never silently become visible.
  ensure(re.match(re.escape(candidate['nameToken'])+r'(?:은|는|이|가|께서)\s',d['quote']) is not None,'SCENE_PORTRAIT_ACTION_SUBJECT_REQUIRED')
  ensure(re.search(r'손을\s*뻗|품에\s*안|물었|말했|말을\s*건|바라보|서\s*있|앉|들어왔|걸어|고개|웃었|울었|달려',d['quote']),'SCENE_PORTRAIT_VISIBLE_ACTION_REVIEW_REQUIRED')
  c=byid[d['npcId']];active=c.get('activePortraitId');status={'npcId':c['id'],'name':c['name'],'presenceEvidence':copy.deepcopy(d),'npcRevision':npcs['revision'],'npcEnvelopeHash':digest(npcs),'characterBindingHash':digest(c)}
  if not active:status.update(status='blocked',code='MAIN_PORTRAIT_UNASSIGNED');blockers.append({'npcId':c['id'],'code':status['code']});selected.append(status);continue
  ensure(isinstance(active,str) and re.fullmatch(r'[a-f0-9]{64}\.png',active),'SCENE_PORTRAIT_MAIN_ASSET_ID_INVALID')
  entries=[p for p in c.get('portraits',[]) if p.get('assetId')==active]
  if len(entries)!=1:status.update(status='blocked',code='MAIN_PORTRAIT_ENTRY_MISSING_OR_DUPLICATE');blockers.append({'npcId':c['id'],'code':status['code']});selected.append(status);continue
  if active in used_assets:status.update(status='blocked',code='DUPLICATE_MAIN_PORTRAIT_ASSET');blockers.append({'npcId':c['id'],'code':status['code'],'otherNpcId':used_assets[active]});selected.append(status);continue
  used_assets[active]=c['id'];entry=entries[0];source_ref=entry.get('sourceRef','');native=re.search(r'(?:^|;)File:(asset\.[a-f0-9]+)(?:;|$)',source_ref)
  status.update(status='selected',mainPortraitAssetId=active,sha256=active[:-4],assetVersion={'scheme':'sha256-content-address','value':active[:-4]},portraitEntryHash=digest(entry),registeredFileAssetId=native.group(1) if native else None,selectionAuthority='canonical NPC activePortraitId; not first/latest/label/filename prose')
  selected.append(status)
 for identifier in set(candidates)-set(decisions):blockers.append({'npcId':identifier,'code':'PRESENCE_REVIEW_COVERAGE_REQUIRED'})
 if not selected and not blockers:blockers.append({'code':'NO_VISIBLE_PARTICIPANTS'})
 result={'schemaVersion':VERSION,'target':copy.deepcopy(target),'sourceBindings':{'story':binding(story),'npcs':binding(npcs)},'nodeHash':digest(node),'presenceReviewHash':digest(review),'selected':selected,'excluded':excluded,'blockers':blockers,'generationReady':not blockers and bool(selected),'inferenceLimit':'Exact canonical names and node-associated relation are candidates. Reviewed current narrative subject/actions determine visibility; unsupported/ambiguous text is blocked.'}
 result['selectionHash']=digest(result);return result

def verify_selection(selection):
 ensure(selection.get('schemaVersion')==VERSION and selection.get('selectionHash')==digest({k:v for k,v in selection.items() if k!='selectionHash'}),'SCENE_PORTRAIT_SELECTION_HASH_INVALID')
 return selection
