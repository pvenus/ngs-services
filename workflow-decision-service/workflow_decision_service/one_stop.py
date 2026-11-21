"""Deterministic OneStop DAG definition; never runs workers or infers motion presets.
Previously persisted plans are resumed by their original executor, not rewritten.
"""
import copy,hashlib,json,re
from pathlib import Path

_DIRECTION_STREAMING={
 'directions':['n','ne','e','se','s','sw','w','nw'],
 'poseGates':{'idle_8way':'pose_idle','combat_8way':'pose_combat'},
 'media':{'walk':'idle_8way','run':'idle_8way','combat_{index}':'combat_8way'},
}

# Prototype selection is library-only. Existing HTTP/stack options never emit
# v5 until scoped compare-and-swap execution support has been reviewed.
_REPRESENTATIVE_FIRST={
 'planning_idle':['planning_representative'],
 'planning_combat':['planning_representative'],
 'portrait_representative':['planning_representative'],
 'portrait_idle':['planning_idle','portrait_representative'],
 'portrait_combat':['planning_combat','portrait_representative'],
 'idle_8way':['portrait_idle'],
 'combat_8way':['portrait_combat'],
}

def digest(value):
 return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def _stream_directions(stages,request,settings):
 if settings!=_DIRECTION_STREAMING:raise ValueError('ONE_STOP_DIRECTION_REGISTRY_INVALID')
 motion=[('walk','walk'),('run','run')]
 if request['includeCombat']:
  motion.extend(('combat_{index}',f'combat_{i}') for i,_ in enumerate(request['combatMotionPresets']))
 by_id={stage['id']:stage for stage in stages}
 removed={f'{kind}_{variant}' for _,variant in motion for kind in ('animation','frames')}
 if not removed<=by_id.keys():raise ValueError('ONE_STOP_DIRECTION_REGISTRY_INVALID')
 streamed=[stage for stage in stages if stage['id'] not in removed]
 def stage(stage_id,depends):return {'id':stage_id,'dependsOn':depends,'completion':'verified-publication'}
 directions=settings['directions']
 for producer,gate in settings['poseGates'].items():
  if producer not in by_id:continue
  # The aggregate remains a required terminal stage, but is deliberately
  # not an ancestor of its read-only per-direction publication gates.
  streamed.extend(stage(f'{gate}_{d}',copy.deepcopy(by_id[producer]['dependsOn'])) for d in directions)
 for template,variant in motion:
  gate=settings['poseGates'][settings['media'][template]]
  for d in directions:
   raw=f'animation_{variant}_{d}'
   streamed.extend((stage(raw,[f'{gate}_{d}']),stage(f'frames_{variant}_{d}',[raw])))
 return streamed

def stages_from(config,request):
 if config.get('schemaVersion')!='ngs.npc-one-stop-registry.v2' or config.get('version') not in ('2','3','4','5'):raise ValueError('ONE_STOP_REGISTRY_INVALID')
 stages=copy.deepcopy(config['stages'])
 if request['includeCombat']:
  stages.extend(copy.deepcopy(config['combatStages']))
  for index,_preset in enumerate(request['combatMotionPresets']):
   for template in config['combatMotionStages']:
    stages.append({'id':template['id'].format(index=index),'dependsOn':[d.format(index=index)for d in template['dependsOn']],'completion':template['completion']})
 if config['version'] in ('3','4','5'):
  if request['includeCombat']:
   overrides=config.get('combatDependencyOverrides',{})
   if not isinstance(overrides,dict) or set(overrides)-{s['id']for s in stages}:raise ValueError('ONE_STOP_DEPENDENCIES_INVALID')
   for stage in stages:
    if stage['id']in overrides:stage['dependsOn']=copy.deepcopy(overrides[stage['id']])
  if config['version']=='5':
   order=config.get('representativeFirst')
   if order!=_REPRESENTATIVE_FIRST:raise ValueError('ONE_STOP_REPRESENTATIVE_REGISTRY_INVALID')
   for stage in stages:
    if stage['id'] in order:stage['dependsOn']=copy.deepcopy(order[stage['id']])
  if config['version'] in ('4','5'):stages=_stream_directions(stages,request,config.get('directionStreaming'))
  # Explicit dependency graph, stable topological order; no runtime rewriting.
  pending=stages;stages=[];ready=set()
  while pending:
   available=[stage for stage in pending if isinstance(stage.get('dependsOn'),list) and all(d in ready for d in stage['dependsOn'])]
   if not available:raise ValueError('ONE_STOP_DEPENDENCIES_INVALID')
   for stage in available:
    stages.append(stage);ready.add(stage['id']);pending.remove(stage)
 seen=set()
 for stage in stages:
  if set(stage)!={'id','dependsOn','completion'} or not isinstance(stage['id'],str) or not re.fullmatch('[a-z][a-z0-9_]*',stage['id']) or stage['completion']!='verified-publication' or not isinstance(stage['dependsOn'],list):raise ValueError('ONE_STOP_STAGE_INVALID')
  if stage['id']in seen or len(set(stage['dependsOn']))!=len(stage['dependsOn']) or any(d not in seen for d in stage['dependsOn']):raise ValueError('ONE_STOP_DEPENDENCIES_INVALID')
  seen.add(stage['id'])
 return stages

def select(request,*,direction_streaming=False,representative_first=False):
 if not isinstance(request,dict) or request.get('schemaVersion')!='ngs.npc-one-stop.v1' or request.get('projectId')!='binari' or type(request.get('includeCombat'))is not bool:
  raise ValueError('ONE_STOP_REQUEST_REQUIRED')
 if request['includeCombat']:
  presets=request.get('combatMotionPresets')
  if not isinstance(presets,list)or not 1<=len(presets)<=16 or any(not isinstance(p,str)or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}',p)for p in presets)or len(set(presets))!=len(presets):raise ValueError('ONE_STOP_COMBAT_PRESETS_REQUIRED')
 elif 'combatMotionPresets'in request:raise ValueError('ONE_STOP_NONCOMBAT_PRESETS_FORBIDDEN')
 config=json.loads(Path(__file__).with_name('one_stop_registry.json').read_text())
 if representative_first is True and direction_streaming is not True:raise ValueError('ONE_STOP_REPRESENTATIVE_DIRECTION_REQUIRED')
 if direction_streaming is True:
  config={**config,'version':'4','directionStreaming':_DIRECTION_STREAMING}
 if representative_first is True:
  config={**config,'version':'5','representativeFirst':_REPRESENTATIVE_FIRST}
 stages=stages_from(config,request)
 return {'schemaVersion':'ngs.npc-one-stop-plan.v2','version':config['version'],'requestHash':digest(request),'registryHash':digest(config),'planHash':digest(stages),'stages':stages,'modelCalls':0,'workerCalls':0}
