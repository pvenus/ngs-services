"""Three cinematic alternatives of one canonical standoff; no gameplay mutation."""
def validate(plan,sources):
 from .ui_mockup_contract import digest
 from .episode_visual_contract import episode
 if set(plan)!={'schemaVersion','target','sourceBindings','requests','maximumImageCalls'}or plan['schemaVersion']!='npc-cinematic-scene-sample-plan.v1'or plan['maximumImageCalls']!=3 or len(plan['requests'])!=1:raise ValueError('CINEMATIC_EXACT_THREE')
 if plan['sourceBindings']!={k:digest(v)for k,v in sources.items()}:raise ValueError('SOURCE_CHANGED_REVIEW_REQUIRED')
 if plan['target']!={'projectId':'binari','actId':'story.main.act01','chapterId':'chapter01','episodeId':'chapter01-01_episode1'}:raise ValueError('CINEMATIC_TARGET')
 r=plan['requests'][0]
 if set(r)!={'key','kind','peoplePolicy','identity','backgroundAsset','slots','setActive','applyToStory'}or r['key']!='episode1.standoff.cinematic.20261022'or r['kind']!='cinematic-scene-sample'or r['peoplePolicy']!='required'or r['setActive']is not False or r['applyToStory']is not False:raise ValueError('CINEMATIC_SCOPE')
 npc=next(n for n in sources['npcs']['document']['characters']if n['id']=='enemy.aekmagi.shield')
 if r['identity']!={'npcId':npc['id'],'mainPortraitId':npc['activePortraitId']}or not any(p['assetId']==npc['activePortraitId']for p in npc['portraits']):raise ValueError('CINEMATIC_CURRENT_IDENTITY')
 if r['backgroundAsset']!={'fileAssetId':'asset.1431b0f9cd987b1cf2340a52bec2e54261dac39b39df41b1','sha256':'a05e5d47eaf1623a15047fcf34e5b6586bae1ea33acf0f4559fc5f0356584a38'}:raise ValueError('CINEMATIC_BACKGROUND')
 node=next(n for n in episode(sources,plan['target'])['playFlow']['nodes']if n['id']=='standoff')
 if len(r['slots'])!=3 or [s.get('index')for s in r['slots']]!=[0,1,2]:raise ValueError('CINEMATIC_THREE_ALTERNATIVES')
 for s in r['slots']:
  if set(s)!={'index','variantKey','emotion','pose','anchors','intent'}or any(s[k]is not None for k in ['variantKey','emotion','pose'])or s['anchors']!=[{'nodeId':'standoff','quote':node['body']}]or not isinstance(s['intent'],str)or not s['intent'].strip():raise ValueError('CINEMATIC_VISIBLE_SOURCE')
 return {'planHash':digest(plan),'maximumImageCalls':3,'requestCount':1,'canonicalWrites':0}
