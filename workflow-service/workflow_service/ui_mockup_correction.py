"""One source-bound correction of the failed third mockup; no canonical writes."""
def validate(plan,sources):
 from .ui_mockup_contract import digest
 from .episode_visual_contract import episode
 if set(plan)!={'schemaVersion','target','sourceBindings','requests','maximumImageCalls'} or plan['schemaVersion']!='npc-ui-mockup-correction-plan.v1' or plan['maximumImageCalls']!=1 or len(plan['requests'])!=1:raise ValueError('MOCKUP_CORRECTION_EXACT_ONE')
 if plan['sourceBindings']!={k:digest(v)for k,v in sources.items()}:raise ValueError('SOURCE_CHANGED_REVIEW_REQUIRED')
 if plan['target']!={'projectId':'binari','actId':'story.main.act01','chapterId':'chapter01','episodeId':'chapter01-01_episode1'}:raise ValueError('MOCKUP_CORRECTION_TARGET')
 r=plan['requests'][0]
 if set(r)!={'key','kind','peoplePolicy','identity','backgroundAsset','slots','setActive','applyToStory','corrects'} or r['key']!='episode1.share-ui.step3.correction.20261022' or r['kind']!='ui-mockup-correction' or r['peoplePolicy']!='excluded' or r['identity']is not None or r['setActive']is not False or r['applyToStory']is not False:raise ValueError('MOCKUP_CORRECTION_SCOPE')
 if r['corrects']!={'requestId':'visual.request.a31d0699c410c136543bba3119f92b74748eff1d','outputId':'visual.request.a31d0699c410c136543bba3119f92b74748eff1d.output.3','sha256':'2979f85a53c6f101edb90e3868b4dd0822b15f6c8fe0411c7324675d37dfc213'}:raise ValueError('MOCKUP_CORRECTION_PARENT')
 if r['backgroundAsset']!={'fileAssetId':'asset.1431b0f9cd987b1cf2340a52bec2e54261dac39b39df41b1','sha256':'a05e5d47eaf1623a15047fcf34e5b6586bae1ea33acf0f4559fc5f0356584a38'}:raise ValueError('MOCKUP_CORRECTION_BACKGROUND')
 nodes={n['id']:n for n in episode(sources,plan['target'])['playFlow']['nodes']};slots=r['slots']
 if len(slots)!=1 or slots[0].get('index')!=0:raise ValueError('MOCKUP_CORRECTION_ONE_SLOT')
 s=slots[0]
 if set(s)!={'index','variantKey','emotion','pose','anchors','intent'}or any(s[k]is not None for k in ['variantKey','emotion','pose'])or s['anchors']!=[{'nodeId':'inquiry.wheels','quote':nodes['inquiry.wheels']['body']}]or not isinstance(s['intent'],str)or not s['intent'].strip():raise ValueError('MOCKUP_CORRECTION_SOURCE')
 return {'planHash':digest(plan),'maximumImageCalls':1,'requestCount':1,'canonicalWrites':0}
