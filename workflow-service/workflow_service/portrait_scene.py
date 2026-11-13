"""Portrait-reference scene compiler v1; never executes a native image tool."""
from .scene_portraits import digest,verify_selection
VERSION='npc-node-main-portrait-prompt.v1'
def compile_descriptor(packet,message,plan,selection,manifest):
 verify_selection(selection)
 if not isinstance(message,str) or len(message)>12000:raise ValueError('PORTRAIT_SCENE_REQUEST_MESSAGE_INVALID')
 if set(plan)!={'schemaVersion','sourcePacketHash','sceneSentence','styleQuotes'} or plan['schemaVersion']!='npc-node-portrait-shot-plan.v1' or plan['sourcePacketHash']!=digest(packet):raise ValueError('PORTRAIT_SCENE_PLAN_SOURCE_INVALID')
 if manifest['selectionHash']!=selection['selectionHash'] or manifest.get('manifestHash')!=digest({k:v for k,v in manifest.items() if k!='manifestHash'}):raise ValueError('PORTRAIT_SCENE_MANIFEST_BINDING_INVALID')
 if packet['target']!=selection['target'] or packet['scene']['nodeHash']!=selection['nodeHash'] or any(packet['sourceBindings'][k]!=selection['sourceBindings'][k] for k in ['story','npcs']):raise ValueError('PORTRAIT_SCENE_FORMAL_SOURCE_CHANGED')
 if not selection['generationReady'] or not manifest['generationReady']:return {'compilerVersion':VERSION,'generationReady':False,'arguments':None,'blockers':manifest['blockers'],'sourcePacketHash':digest(packet),'referenceSelectionHash':selection['selectionHash'],'referenceManifestHash':manifest['manifestHash']}
 refs=manifest['references'];selected={r['npcId']:r for r in selection['selected']}
 if len(refs)!=len(selected) or len({r['npcId'] for r in refs})!=len(refs) or set(r['npcId'] for r in refs)!=set(selected):raise ValueError('PORTRAIT_SCENE_REFERENCE_COVERAGE_INVALID')
 for ref in refs:
  pin=selected[ref['npcId']]
  if any(ref[k]!=pin[k] for k in ['mainPortraitAssetId','sha256','assetVersion','npcRevision','portraitEntryHash']):raise ValueError('PORTRAIT_SCENE_WRONG_CHARACTER_REFERENCE')
 text=plan['sceneSentence']
 if not isinstance(text,str) or not 20<=len(text)<=500 or '\n' in text or any(s in text for s in ['1안','2안','3안','다음 장면','첫 장면']):raise ValueError('PORTRAIT_SCENE_ONE_MOMENT_REQUIRED')
 for r in refs:
  name='어머니' if r['npcId']=='npc.missing_child_mother' else r['name']
  if name not in text:raise ValueError('PORTRAIT_SCENE_VISIBLE_PERSON_OMITTED')
 for pin in selected.values():
  quote=pin['presenceEvidence']['quote']
  if '손을 뻗' in quote and '손을 뻗' not in text:raise ValueError('PORTRAIT_SCENE_VISIBLE_ACTION_OMITTED')
  if '그림을 품' in quote and ('그림' not in text or '품' not in text):raise ValueError('PORTRAIT_SCENE_VISIBLE_PROP_OMITTED')
 style=next(s['value'] for s in packet['styleRules'] if s['id']=='visualStyle')
 if not isinstance(plan['styleQuotes'],list) or not 1<=len(plan['styleQuotes'])<=4 or any(not isinstance(q,str) or not 5<=len(q)<=180 or q not in style for q in plan['styleQuotes']):raise ValueError('PORTRAIT_SCENE_STYLE_NOT_IN_MASTER')
 identities=' '.join('참조 이미지 '+str(i+1)+'는 '+r['name']+'('+r['npcId']+') 한 사람의 얼굴·머리·체형·복장 식별 기준이다.' for i,r in enumerate(refs))
 prompt='한 순간, 한 카메라의 한 장면 삽화. 각 인물이 한 번씩만 보이는 하나의 연속된 구도다. '+text+' '+identities+' 서로 다른 인물의 외형을 합치지 않고 각 참조 인물의 정체성을 유지한다. 포트레이트의 배경·포즈·화풍을 장면 설정으로 옮기지 않는다. 현재 장면과 행동은 스토리를 따른다. 화풍은 '+', '.join(plan['styleQuotes'])+'이다. 그림을 품에 안은 동작을 유지하고 그려진 면은 몸 쪽으로 가려 내용은 보이지 않는다. 구체 장소를 새로 정하지 않는 닥종이·담채 바탕이며 글자와 분할 테두리가 없는 한 화면이다.'
 args={'prompt':prompt,'transparent_background':False,'referenced_image_paths':[r['localPath'] for r in refs]}
 meta={'sourceBindings':packet['sourceBindings'],'sourcePacketHash':digest(packet),'referenceSelection':selection,'referenceManifest':manifest,'requestMessage':message,'requestMessageHash':digest(message),'sourceRoles':{'masterConcept':'style and visual restrictions','story':'visible people/current actions/current props','mainPortraits':'each bound person appearance only','requestMessage':'supplemental user request, not source canon'},'sourceStatuses':{c['data']['id']:c['data'].get('status') for c in packet['characters']},'providerRequestHash':digest(args)}
 return {'compilerVersion':VERSION,'generationReady':True,'providerId':'codex.image_gen','toolName':'image_gen.imagegen','arguments':args,'referenceOrder':[{'index':i+1,'npcId':r['npcId'],'assetId':r['mainPortraitAssetId'],'sha256':r['sha256']} for i,r in enumerate(refs)],'metadata':meta,'metadataHash':digest(meta)}
