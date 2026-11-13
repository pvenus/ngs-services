"""Explicit connected UI mockup sequence; never a gameplay or portrait asset."""
import hashlib,json,re
SCHEMA='npc-ui-mockup-sequence-plan.v1'
def digest(v):return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def validate(plan,sources):
 if plan.get('schemaVersion')=='npc-selective-spirit-vision-sample-plan.v1':
  from .spirit_vision_contract import validate as spirit_vision
  return spirit_vision(plan,sources)
 if plan.get('schemaVersion')=='npc-current-emotion-vision-sample-plan.v1':
  from .emotion_vision_contract import validate as emotion_vision
  return emotion_vision(plan,sources)
 if plan.get('schemaVersion')=='npc-connected-impact-sequence-plan.v1':
  from .impact_sequence_contract import validate as connected
  return connected(plan,sources)
 if plan.get('schemaVersion')=='npc-cinematic-scene-sample-plan.v1':
  from .cinematic_contract import validate as cinematic
  return cinematic(plan,sources)
 if plan.get('schemaVersion')=='npc-ui-mockup-correction-plan.v1':
  from .ui_mockup_correction import validate as correction
  return correction(plan,sources)
 from .episode_visual_contract import episode
 if set(plan)!={'schemaVersion','target','sourceBindings','requests','maximumImageCalls'} or plan['schemaVersion']!=SCHEMA:raise ValueError('MOCKUP_PLAN_SCHEMA')
 if plan['sourceBindings']!={k:digest(v) for k,v in sources.items()}:raise ValueError('SOURCE_CHANGED_REVIEW_REQUIRED')
 if plan['target']!={'projectId':'binari','actId':'story.main.act01','chapterId':'chapter01','episodeId':'chapter01-01_episode1'}:raise ValueError('MOCKUP_TARGET_SCOPE')
 if len(plan['requests'])!=1 or plan['maximumImageCalls']!=3:raise ValueError('MOCKUP_EXACT_THREE')
 r=plan['requests'][0]
 if set(r)!={'key','kind','peoplePolicy','identity','backgroundAsset','slots','setActive','applyToStory'} or r['kind']!='ui-mockup-sequence' or r['key']!='episode1.share-ui.sequence.20261022' or r['peoplePolicy']!='optional' or r['setActive']is not False or r['applyToStory']is not False:raise ValueError('MOCKUP_PURPOSE_SCOPE')
 ident=r['identity'];n=next((n for n in sources['npcs']['document']['characters'] if n['id']=='enemy.aekmagi.shield'),None)
 if not n or ident!={'npcId':n['id'],'mainPortraitId':n.get('activePortraitId')} or not any(p['assetId']==ident['mainPortraitId']for p in n['portraits']):raise ValueError('MOCKUP_CURRENT_IDENTITY_REQUIRED')
 if r['backgroundAsset']!={'fileAssetId':'asset.1431b0f9cd987b1cf2340a52bec2e54261dac39b39df41b1','sha256':'a05e5d47eaf1623a15047fcf34e5b6586bae1ea33acf0f4559fc5f0356584a38'}:raise ValueError('MOCKUP_BACKGROUND_SCOPE')
 nodes={n['id']:n for n in episode(sources,plan['target'])['playFlow']['nodes']}
 if len(r['slots'])!=3 or [s['index']for s in r['slots']]!=[0,1,2]:raise ValueError('MOCKUP_THREE_CONNECTED_STEPS')
 for slot,node in zip(r['slots'],['standoff','observe','inquiry.wheels']):
  if set(slot)!={'index','variantKey','emotion','pose','anchors','intent'} or any(slot[k]is not None for k in ['variantKey','emotion','pose']):raise ValueError('MOCKUP_NOT_PORTRAIT_VARIANT')
  if len(slot['anchors'])!=1 or slot['anchors'][0]!={'nodeId':node,'quote':nodes[node]['body']}:raise ValueError('MOCKUP_VISIBLE_SOURCE_BINDING')
  if not isinstance(slot['intent'],str) or not slot['intent'].strip():raise ValueError('MOCKUP_DIRECTION_REQUIRED')
 return {'planHash':digest(plan),'requestCount':1,'maximumImageCalls':3,'canonicalWrites':0,'providerCalls':0}
