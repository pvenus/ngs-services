"""Shared Laya/PDS schema and installed-adapter admission contract.
Knowledge can compose registered capabilities, but cannot invent executable code.
"""
ADAPTERS={'npc-field-edit','motion-preset-registration','npc-animation-frames','npc-pose-expansion','npc-animation-batch','npc-animation-experiment','npc-animation','npc-category-modify','npc-category-delete','npc-category-add','npc-lifecycle','npc-category','npc-portrait','npc-category-compare','npc-portrait-compare','npc-lifecycle-delete','npc-lifecycle-restore-backup','knowledge-mutation','concept-modify'}
# These stage adapters execute only via the durable background Planner queue.
# They are not NPC adapters and must not be routed to the legacy NPC runtime.
BACKGROUND_QUEUE_ADAPTERS={'background.cinematic-source':('image',1),'background.pixel-image':('image',1),
                           'background.pixel-correct':('postprocess',0)}

def validate_registry(registry):
 if not isinstance(registry,dict) or registry.get('schemaVersion')!='ngs.worker-registry.v1' or not registry.get('version'):raise ValueError('LAYA_REGISTRY_INVALID')
 threshold=registry.get('selectionPolicy',{}).get('minimumScore')
 if isinstance(threshold,bool) or not isinstance(threshold,(int,float)) or not 0<=threshold<=1:raise ValueError('LAYA_SCORE_THRESHOLD_INVALID')
 actions=registry.get('actions',[]);by={a['id']:a for a in actions}
 if not actions or len(by)!=len(actions):raise ValueError('LAYA_REGISTRY_IDS_INVALID')
 for a in actions:
  if not a.get('workerId') or not a.get('description') or not a.get('inputs') or not a.get('outputs'):raise ValueError('LAYA_REGISTRY_METADATA_INVALID')
  if registry.get('selectionPolicy',{}).get('executionContract')=='worker-count.v1':
   policy=a.get('outputPolicy',{});count=policy.get('count')
   if isinstance(count,bool) or not isinstance(count,int) or count<0 or count!=a.get('candidateCount') or not policy.get('selection') or not policy.get('apply'):raise ValueError('LAYA_OUTPUT_POLICY_INVALID')
  for field in ('dependsOn','requires','provides'):
   if not isinstance(a.get(field),list) or any(not isinstance(x,str) for x in a[field]):raise ValueError('LAYA_REGISTRY_METADATA_INVALID')
  if 'pixelPipeline' in a:
   pipeline=a['pixelPipeline']
   expected=[{'id':'portrait','dependsOn':[]},{'id':'pixel-source','dependsOn':['portrait'],'reference':'portrait','kind':'image-generation','style':'slightly-sd-pixel-portrait'},{'id':'pixel-postprocess','dependsOn':['pixel-source'],'input':'pixel-source','kind':'pixel-postprocess'}]
   if a.get('workerId')!='npc-portrait' or pipeline!=expected:raise ValueError('LAYA_PIXEL_PIPELINE_CONTRACT')
  if 'posePreset' in a:
   if a.get('workerId')!='npc-portrait' or a['posePreset'] not in ('representative','combat','idle'):raise ValueError('LAYA_PORTRAIT_POSE_INVALID')
   reference=a.get('referenceStage')
   if a['posePreset']!='representative' and (reference not in a['dependsOn'] or by.get(reference,{}).get('posePreset')!='representative'):raise ValueError('LAYA_PORTRAIT_REFERENCE_REQUIRED')
   # Idle uses representative identity; combat is an optional workflow stage.
  if 'poseDependencies' in a or 'referencePose' in a:
   deps=a.get('poseDependencies');refs=a.get('referencePose')
   if not isinstance(deps,dict) or set(deps)!={'representative','combat','idle'} or deps['representative']!=[] or deps['combat']!=['representative'] or set(deps['idle']) not in ({'representative'},{'representative','combat'}) or refs!={'combat':'representative','idle':'representative'}:raise ValueError('LAYA_PORTRAIT_POSE_POLICY_INVALID')
  if not set(a['dependsOn'])<=by.keys():raise ValueError('LAYA_REGISTRY_DEPENDENCY_UNKNOWN')
 pending=set(by);seen=set();available=set()
 while pending:
  ready=[i for i in pending if set(by[i]['dependsOn'])<=seen and set(by[i]['requires'])<=available]
  if not ready:raise ValueError('LAYA_REGISTRY_DATA_CONNECTION_INVALID')
  for i in ready:available.update(by[i]['provides']);seen.add(i);pending.remove(i)
 for spec in registry.get('requestTypes',{}).values():
  ids=spec.get('stageIds')
  if not isinstance(ids,list) or not ids or len(ids)!=len(set(ids)) or not set(ids)<=by.keys():raise ValueError('LAYA_REQUEST_TYPE_INVALID')
  for variant in spec.get('stageVariants',[]):
   if not isinstance(variant,dict) or set(variant)!={'when','stageIds'} or not isinstance(variant['when'],dict) or not variant['when'] or any(not isinstance(k,str) or type(v)is not bool for k,v in variant['when'].items()):raise ValueError('WORKFLOW_STAGE_VARIANT_INVALID')
   chosen=variant['stageIds']
   if not isinstance(chosen,list) or len(chosen)<2 or len(chosen)!=len(set(chosen)) or not set(chosen)<=set(ids) or any(not set(by[i]['dependsOn'])<=set(chosen) for i in chosen):raise ValueError('WORKFLOW_STAGE_VARIANT_DEPENDENCY_INVALID')
  eligible=spec.get('eligibleStageIds',ids)
  if not isinstance(eligible,list) or not set(eligible)<=by.keys():raise ValueError('LAYA_REQUEST_TYPE_ELIGIBLE_INVALID')
  if len(ids)>1:
   # Dependency closure is mandatory for aggregate creation. Singles use existing data.
   if any(not set(by[i]['dependsOn'])<=set(ids) for i in ids):raise ValueError('LAYA_REQUEST_TYPE_DEPENDENCY_MISSING')
 return registry


def validate_active_registry(registry, *, require_knowledge_management=True):
 validate_registry(registry)
 for action in registry['actions']:
  worker=action['workerId'];policy=action.get('outputPolicy',{})
  if worker in ('image','postprocess'):
   expected=BACKGROUND_QUEUE_ADAPTERS.get(action['id'])
   if (expected!=(worker,action.get('modelCalls')) or
       action.get('requestRequirements',{}).get('schemaVersion',{}).get('const')!='ngs.background-pixel-workflow.v1' or
       registry['requestTypes'].get('background.pixel.workflow',{}).get('stageIds')!=list(BACKGROUND_QUEUE_ADAPTERS) or
       policy.get('count')!=1 or policy.get('kind')!='single-result' or policy.get('selection')!='none' or
       policy.get('apply')!='authorized-mutation'):
    raise ValueError('LAYA_BACKGROUND_QUEUE_ADAPTER_CONTRACT')
  elif worker=='document':
   mode=action['id']
   if (mode not in ('map.generate','map.update','assembly.generate','assembly.update') or action.get('modelCalls')!=(3 if mode=='map.generate' else 1 if mode=='assembly.generate' else 0) or
       action.get('dependsOn')!=[] or registry['requestTypes'].get(mode,{}).get('stageIds')!=[mode] or
       action.get('requestRequirements',{}).get('schemaVersion')!=(
        {'type':'string','enum':['ngs.map-generate.v1','ngs.map-generate.v2']} if mode=='map.generate' else
        {'type':'string','enum':['ngs.assembly-generate.v1','ngs.assembly-generate.v2']} if mode=='assembly.generate' else
        {'type':'string','const':'ngs.'+mode.replace('.','-')+'.v1'}) or
       policy.get('count')!=1 or policy.get('kind')!='single-result' or policy.get('selection')!='none' or policy.get('apply')!='authorized-mutation'):
    raise ValueError('LAYA_MAP_DOCUMENT_ADAPTER_CONTRACT')
  elif worker=='file':
   mode=action['id']
   if (mode!='file.update' or action.get('modelCalls')!=0 or action.get('dependsOn')!=[] or
       registry['requestTypes'].get(mode,{}).get('stageIds')!=[mode] or
       action.get('requestRequirements',{}).get('schemaVersion')!={'type':'string','const':'ngs.file-update.v1'} or
       policy.get('count')!=1 or policy.get('kind')!='single-result' or policy.get('selection')!='none' or
       policy.get('apply')!='authorized-mutation'):
    raise ValueError('LAYA_FILE_METADATA_ADAPTER_CONTRACT')
  elif worker=='animation':
   stage_ids=['background.animation-cinematic','background.animation-pixel']
   pixel=action['id']=='background.animation-pixel'
   expected_pipeline=[
    {'id':'wan','operation':'media.animation.render','dependsOn':[]},
    {'id':'upscale','operation':'media.frames.upscale','dependsOn':['wan']},
    *([{'id':'pixel_correct','operation':'media.frames.pixel-correct','dependsOn':['upscale']}] if pixel else []),
    {'id':'publish','operation':'media.sequence.publish','dependsOn':['pixel_correct' if pixel else 'upscale']},
   ]
   if (action['id'] not in stage_ids or action.get('modelCalls')!=2 or action.get('dependsOn')!=[] or
       action.get('executionStages')!=expected_pipeline or
       action.get('requestRequirements',{}).get('schemaVersion',{}).get('const')!='ngs.background-animation-workflow.v2' or
       registry['requestTypes'].get('background.animation.workflow',{}).get('stageIds')!=stage_ids or
       policy.get('count')!=1 or policy.get('kind')!='single-result' or policy.get('selection')!='none' or
       policy.get('apply')!='authorized-mutation'):
    raise ValueError('LAYA_BACKGROUND_ANIMATION_ADAPTER_CONTRACT')
  elif worker not in ADAPTERS:raise ValueError('LAYA_EXECUTION_ADAPTER_UNKNOWN')
  if worker.endswith('-compare'):
   if policy.get('count')!=3 or policy.get('kind')!='comparison' or policy.get('apply')!='after-selection':raise ValueError('LAYA_COMPARISON_ADAPTER_CONTRACT')
  elif policy.get('kind')=='comparison':raise ValueError('LAYA_COMPARISON_ADAPTER_REQUIRED')
  if worker=='motion-preset-registration' and (action['id']!='motion-preset.register' or action.get('modelCalls')!=0 or policy.get('count')!=1 or policy.get('kind')!='single-result' or policy.get('selection')!='none' or policy.get('apply')!='authorized-mutation'):raise ValueError('LAYA_MOTION_PRESET_REGISTRATION_CONTRACT')
  if worker=='npc-animation-frames' and (action['id']!='animation.extract-frames' or action.get('modelCalls')!=0 or policy.get('count')!=1 or policy.get('kind')!='single-result' or policy.get('selection')!='none' or policy.get('apply')!='authorized-mutation'):raise ValueError('LAYA_ANIMATION_FRAMES_ADAPTER_CONTRACT')
  if worker=='npc-pose-expansion' and (action['id']not in ('pose.expand-eight-directions','pose.regenerate-one') or action.get('modelCalls')!=(1 if action['id']=='pose.regenerate-one'else 8) or policy.get('count')!=1 or policy.get('kind')!='single-result' or policy.get('selection')!='none' or policy.get('apply')!='authorized-mutation'):raise ValueError('LAYA_POSE_EXPANSION_ADAPTER_CONTRACT')
  if worker=='npc-animation-batch' and (action['id']!='animation.generate-eight-directions' or action.get('modelCalls')!=0 or policy.get('count')!=1 or policy.get('selection')!='none' or policy.get('apply')!='authorized-mutation'):raise ValueError('LAYA_ANIMATION_BATCH_ADAPTER_CONTRACT')
  if worker=='npc-animation-experiment' and (action['id']!='animation.experiment' or action.get('modelCalls')!=1 or policy.get('count')!=1 or policy.get('selection')!='none' or policy.get('apply')!='authorized-mutation'):raise ValueError('LAYA_ANIMATION_EXPERIMENT_ADAPTER_CONTRACT')
  if worker=='npc-animation' and (action['id']not in ('animation.generate','animation.regenerate-one','animation.generate-rear','animation.regenerate-rear','animation.generate-from-pose','animation.regenerate-from-pose') or (action.get('modelCalls')!=(2 if action['id'].endswith('-rear')else 1)) or policy.get('count')!=1 or policy.get('selection')!='none' or policy.get('apply')!='authorized-mutation'):raise ValueError('LAYA_ANIMATION_ADAPTER_CONTRACT')
  if worker=='npc-field-edit' and (action['id']!='field-modify' or action.get('modelCalls')!=0 or policy.get('count')!=1 or policy.get('kind')!='single-result' or policy.get('selection')!='none' or policy.get('apply')!='authorized-mutation'):raise ValueError('WORKFLOW_FIELD_EDIT_ADAPTER_CONTRACT')
  if worker=='concept-modify' and (action['id']!='concept.modify' or policy.get('count')!=1 or policy.get('selection')!='none' or policy.get('apply')!='authorized-mutation'):raise ValueError('LAYA_CONCEPT_ADAPTER_CONTRACT')
  if worker=='knowledge-mutation' and (action['id'] not in ('knowledge.create','knowledge.update','knowledge.delete','knowledge.delete-category') or policy.get('count')!=0 or policy.get('selection')!='none' or policy.get('apply')!='authorized-mutation'):
   raise ValueError('LAYA_KNOWLEDGE_ADAPTER_CONTRACT')
  if worker in ('npc-category','npc-category-compare') and action.get('categoryRole') not in ('overview','art'):raise ValueError('LAYA_CATEGORY_ADAPTER_CONTRACT')
  if worker=='npc-category-add' and (action['id'] not in ('category-add','planning.representative','planning.combat','planning.idle') or action.get('categoryRole')!='new-category' or policy.get('count')!=1):raise ValueError('LAYA_CATEGORY_ADD_ADAPTER_CONTRACT')
  if worker=='npc-category-delete' and (action['id']!='category-delete' or action.get('modelCalls')!=0 or policy.get('count')!=0):raise ValueError('LAYA_CATEGORY_DELETE_ADAPTER_CONTRACT')
  if worker=='npc-category-modify' and (action['id']!='category-modify' or action.get('categoryRole')!='existing-category' or policy.get('count')!=1):raise ValueError('LAYA_CATEGORY_MODIFY_ADAPTER_CONTRACT')
 # Keep the management route available. Disable ordinary tasks through knowledge,
 # but removing the sole update path requires a separately implemented migration.
 by={a['id']:a for a in registry['actions']}
 for operation in (('create','update','delete') if require_knowledge_management else ()):
  ident='knowledge.'+operation
  if by.get(ident,{}).get('workerId')!='knowledge-mutation' or registry['requestTypes'].get(ident,{}).get('stageIds')!=[ident]:raise ValueError('LAYA_KNOWLEDGE_MANAGEMENT_ROUTE_REQUIRED')
 return registry
