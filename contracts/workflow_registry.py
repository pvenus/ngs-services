"""Operator-maintained workflow configuration. No model or request can supply a registry."""
import hashlib,json,os,re
from pathlib import Path
FIELDS=('planningPose','posePreset','id','workerId','presetId','presetVersion','categoryRole','candidateCount','modelCalls','dependsOn','outputPolicy')
def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(v):return hashlib.sha256(canonical(v).encode()).hexdigest()
def execution(action):return {k:action[k] for k in FIELDS if k in action}
import importlib.util
_contract_path=Path(__file__).with_name('worker_registry.py')
_contract_spec=importlib.util.spec_from_file_location('ngs_worker_registry_contract',_contract_path)
_contract=importlib.util.module_from_spec(_contract_spec)
_contract_spec.loader.exec_module(_contract)
validate_registry=_contract.validate_registry
def validate_active_registry(registry):
 return _contract.validate_active_registry(registry, require_knowledge_management=False)

def classify(request_type,registry):
 validate_registry(registry)
 spec=registry.get('requestTypes',{}).get(request_type)
 if spec is None:raise ValueError('LAYA_REQUEST_TYPE_UNREGISTERED')
 ids=spec['stageIds'];result={'requestType':request_type,'mode':'single' if len(ids)==1 else 'aggregate','targetStageIds':ids,'eligibleStageIds':spec.get('eligibleStageIds',ids),'registryVersion':registry['version'],'registryHash':digest(registry)}
 if len(ids)==1:
  action=next(a for a in registry['actions'] if a['id']==ids[0]);result.update(workerId=action['workerId'],stageId=action['id'])
 return result

def validate_choice(choice,registry,target_ids=None,mode="aggregate"):
 validate_registry(registry)
 if not isinstance(choice,dict) or set(choice)!={'stageIds','invocationStageIds','variationIds','score','reason'}:raise ValueError('LAYA_PLAN_SHAPE_INVALID')
 ids=choice['stageIds'];by={a['id']:a for a in registry['actions']}
 if not isinstance(ids,list) or not ids or len(set(ids))!=len(ids):raise ValueError('LAYA_PLAN_DUPLICATE_OR_EMPTY')
 if target_ids is not None:
  match=(len(ids)==1 and ids[0] in target_ids) if mode=='single' else set(ids)==set(target_ids)
  if not match:raise ValueError('LAYA_PLAN_REQUEST_TARGET_MISMATCH')
 seen=set();available=set();stages=[]
 for ident in ids:
  if ident not in by:raise ValueError('LAYA_WORKER_UNREGISTERED')
  a=by[ident]
  if mode=='aggregate' and not set(a['dependsOn'])<=seen:raise ValueError('LAYA_DEPENDENCY_ORDER_INVALID')
  if mode=='aggregate' and not set(a['requires'])<=available:raise ValueError('LAYA_PLAN_DATA_CONNECTION_INVALID')
  seen.add(ident);available.update(a['provides']);stages.append(execution(a))
 validate_invocations(choice,registry,mode)
 return stages

def quality(choice,registry):
 score=choice.get('score');reason=choice.get('reason')
 if isinstance(score,bool) or not isinstance(score,(int,float)) or not 0<=score<=1 or not isinstance(reason,str) or not reason.strip():raise ValueError('LAYA_SELECTION_SCORE_INVALID')
 threshold=registry['selectionPolicy']['minimumScore']
 return {'score':score,'threshold':threshold,'passed':score>=threshold,'basis':'model-self-assessment-uncalibrated','reason':reason}

def validate_invocations(choice,registry,mode):
 ids=choice['stageIds'];by={a['id']:a for a in registry['actions']}
 if registry.get('selectionPolicy',{}).get('executionContract')=='worker-count.v1':
  if choice.get('invocationStageIds')!=ids:raise ValueError('LAYA_WORKER_SINGLE_CALL_REQUIRED')
  if choice.get('variationIds')!=['none']*len(ids):raise ValueError('LAYA_VARIATIONS_OWNED_BY_WORKER')
  return [{'id':i+'.1' if mode=='single' else i,'stageId':i,'candidateCount':by[i]['outputPolicy']['count'],'variation':None,'outputPolicy':by[i]['outputPolicy'],**({'variations':by[i].get('variations',[])} if by[i]['outputPolicy']['kind']=='comparison' else {})}for i in ids]
 count=registry['selectionPolicy'].get('singleInvocationCount')
 if count!=3:raise ValueError('LAYA_SINGLE_INVOCATION_POLICY_INVALID')
 structure_count=registry['selectionPolicy'].get('structureInvocationCount',count)
 if structure_count not in (1,3):raise ValueError('LAYA_STRUCTURE_INVOCATION_POLICY_INVALID')
 def repetitions(ident):
  if mode!='single':return 1
  return structure_count if by[ident].get('generationKind')=='structure' else count
 expected=[i for i in ids for _ in range(repetitions(i))]
 if choice.get('invocationStageIds')!=expected:raise ValueError('LAYA_INVOCATION_COUNT_OR_ORDER_INVALID')
 variations=choice.get('variationIds')
 if not isinstance(variations,list) or len(variations)!=len(expected):raise ValueError('LAYA_VARIATION_COUNT_INVALID')
 result=[];offset=0
 for ident in ids:
  repeats=repetitions(ident)
  creative=mode=='single' and by[ident].get('generationKind')=='candidate'
  presets={v['id']:v for v in by[ident].get('variations',[])}
  selected=variations[offset:offset+repeats]
  if creative:
   if len(set(selected))!=repeats or any(v not in presets for v in selected):raise ValueError('LAYA_VARIATION_DISTINCT_REGISTERED_REQUIRED')
  elif any(v!='none' for v in selected):raise ValueError('LAYA_VARIATION_NOT_APPLICABLE')
  for n in range(repeats):
   result.append({'id':ident+'.'+str(n+1) if mode=='single' else ident,'stageId':ident,'candidateCount':by[ident]['candidateCount'],'variation':presets[selected[n]] if creative else None})
  offset+=repeats
 return result

def retry_policy(registry,mode,stages):
 chosen=[a for a in registry['actions'] if a['id'] in [s['id']for s in stages]]
 if registry.get('selectionPolicy',{}).get('executionContract')=='worker-count.v1':
  creative=mode=='single' and bool(chosen) and all(a.get('outputPolicy',{}).get('kind')=='comparison' for a in chosen)
 else:
  creative=mode=='single' and all(a.get('generationKind') in ('candidate','comparison') for a in chosen)
 limit=registry['selectionPolicy'].get('candidateDuplicateRetryLimit',0)
 if limit not in (0,1):raise ValueError('LAYA_DUPLICATE_RETRY_POLICY_INVALID')
 return {'candidateDuplicateRetryLimit':limit if creative else 0,'trigger':'exact-content-duplicate','nearSimilarity':'flag-only','preserveOriginal':True}

def validate_request_fields(request,registry,action_ids):
 """Validate declared admission fields without retrieving or mutating source data."""
 for action in registry['actions']:
  if action['id'] not in action_ids:continue
  for field in action.get('requiredUserFields', []):
   value = request.get(field)
   if field == 'role':
    character = request.get('character')
    value = character.get('role', value) if isinstance(character, dict) else value
   # These are explicit user inputs, not Python truthiness (False/0 are not text).
   valid = value is True if field == 'cycleConfirmed' else isinstance(value, str) and bool(value.strip())
   if not valid:raise ValueError('WORKFLOW_REQUIRED_USER_FIELD_INVALID:'+field)
  for field,rule in action.get('requestRequirements',{}).items():
   value=request.get(field);kind=rule.get('type')
   valid=(isinstance(value,str) and bool(value.strip())) if kind=='string' else (type(value)is int) if kind=='integer' else (isinstance(value,list) and all(isinstance(x,str) and x.strip() for x in value)) if kind=='string-array' else False
   if valid and 'enum'in rule:valid=value in rule['enum']
   if valid and 'const'in rule:valid=value==rule['const']
   if valid and 'pattern'in rule:valid=re.fullmatch(rule['pattern'],value)is not None
   if valid and 'minimum'in rule:valid=value>=rule['minimum']
   if valid and 'minItems'in rule:valid=len(value)>=rule['minItems']
   if valid and 'minLength'in rule:valid=len(value)>=rule['minLength']
   if valid and rule.get('unique'):valid=len(value)==len(set(value))
   if not valid:raise ValueError('LAYA_REQUIRED_REQUEST_FIELD_INVALID:'+field)
