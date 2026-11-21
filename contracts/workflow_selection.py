"""Data-driven deterministic selection for accepted structured requests.

This is not free-text interpretation. Registered requestType mappings define
capabilities; ambiguity stops. No Laya process, Codex, model or worker call.
"""
import importlib.util
from pathlib import Path


def _module(name):
    spec=importlib.util.spec_from_file_location('ngs_shared_'+name,Path(__file__).with_name(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

r=_module('workflow_registry')
a=_module('workflow_admission')


class SelectionError(ValueError):
    def __init__(self,message,details=None):
        super().__init__(message);self.details=details or {'modelCalls':0,'workerCalls':0}


def _order(ids,registry):
    by={x['id']:x for x in registry['actions']};pending=set(ids);ordered=[]
    while pending:
        ready=[i for i in ids if i in pending and set(by[i]['dependsOn'])<=set(ordered)]
        if not ready:raise ValueError('WORKFLOW_DEPENDENCY_ORDER_INVALID')
        ordered.extend(ready);pending.difference_update(ready)
    return ordered


def select_workflow(payload,snapshot):
    if not isinstance(payload,dict) or set(payload)!={'mode','request','requestType'} or payload.get('mode')not in ('single','aggregate'):
        raise SelectionError('NPC_WORKFLOW_AGGREGATE_REQUEST_REQUIRED')
    request=payload['request'];mode=payload['mode'];request_type=payload['requestType']
    if not isinstance(request_type,str) or not request_type or not isinstance(request,dict) or not isinstance(request.get('schemaVersion'),str):
        raise SelectionError('NPC_WORKFLOW_ACCEPTED_REQUEST_REQUIRED')
    if 'requestType'in request and request['requestType']!=request_type:
        raise SelectionError('WORKFLOW_REQUEST_TYPE_CONFLICT')
    try:
        if mode=='aggregate':a.validate_aggregate(request,request_type)
        a.validate_pose_selection(request)
        registry,source=snapshot();r.validate_active_registry(registry)
        route=r.classify(request_type,registry)
        a.validate_route_request(request, registry['requestTypes'][request_type].get('requestContract'))
        if route['mode']!=mode:raise ValueError('LAYA_REQUEST_MODE_MISMATCH')
        ids=route['targetStageIds']
        variants=registry['requestTypes'][request_type].get('stageVariants',[])
        matched=[v for v in variants if all(k in request and type(request[k])is type(value) and request[k]==value for k,value in v['when'].items())]
        if len(matched)>1:raise ValueError('WORKFLOW_REGISTERED_ROUTE_AMBIGUOUS')
        if matched:ids=matched[0]['stageIds']
        if mode=='single' and (len(route['eligibleStageIds'])!=1 or route['eligibleStageIds']!=ids):
            raise ValueError('WORKFLOW_REGISTERED_ROUTE_AMBIGUOUS')
        if mode=='aggregate':ids=_order(ids,registry)
        if registry.get('selectionPolicy',{}).get('executionContract')!='worker-count.v1':
            raise ValueError('WORKFLOW_EXECUTION_CONTRACT_UNSUPPORTED')
        by={x['id']:x for x in registry['actions']}
        chosen=[by[i]for i in ids]
        r.validate_request_fields(request,registry,ids)
        if any('role'in x.get('requiredUserFields',[])for x in chosen):
            character=request.get('character');nested=character.get('role')if isinstance(character,dict)else None;direct=request.get('role')
            role=nested if nested is not None else direct
            if not isinstance(role,str)or not role.strip():raise ValueError('MISSING_REQUIRED_CHARACTER_ROLE')
            if len(role)>300:raise ValueError('CHARACTER_ROLE_TOO_LONG')
            if nested is not None and direct is not None and nested!=direct:raise ValueError('CONFLICTING_CHARACTER_ROLE')
        reason='Exact registered structured-request contract; deterministic eligibility and IO validation, not model confidence.'
        choice={'stageIds':ids,'invocationStageIds':ids,'variationIds':['none']*len(ids),'score':1.0,'reason':reason}
        stages=r.validate_choice(choice,registry,ids,mode)
        a.validate_pose_selection(request,stages,mode)
        if mode=='single' and 'resultCount'in request:
            if type(request['resultCount'])is not int or request['resultCount']!=max(1,chosen[0]['candidateCount']):
                raise ValueError('LAYA_EXPLICIT_RESULT_COUNT_CONFLICT')
        score=r.quality(choice,registry);score['basis']='deterministic-registered-contract-not-probability'
        current,current_source=snapshot()
        if r.digest(current)!=r.digest(registry)or current_source!=source:raise ValueError('LAYA_REGISTRY_CHANGED_DURING_PLANNING')
        trace={'provider':'workflow-decision-service','modelCalls':0,'workerCalls':0,'cached':False,'requestHash':r.digest(request),
               'registryHash':r.digest(registry),'scoreBasis':score['basis'],'requestType':request_type}
        return {'schemaVersion':'ngs.npc-workflow-selection.v3','mode':mode,'selectionQuality':score,
                'selection':{'presetId':'npc.complete','presetVersion':'1'},'selectionMethod':'registered-workflow-code',
                'modelCalls':0,'contextOwner':'worker','dataReader':'PDS','onFailure':'stop','automaticRetry':False,
                'registryVersion':registry['version'],'registryHash':r.digest(registry),'registry':registry,
                'workflowSource':source,'requestType':request_type,'requestHash':r.digest(request),'plannerTrace':trace,
                'stages':stages,'retryPolicy':r.retry_policy(registry,mode,stages),'invocations':r.validate_invocations(choice,registry,mode)}
    except SelectionError:raise
    except (ValueError,KeyError,TypeError)as exc:raise SelectionError(str(exc))from exc


def route_request(payload,snapshot):
    if not isinstance(payload,dict)or set(payload)!={'requestType'}or not isinstance(payload['requestType'],str):raise SelectionError('REQUEST_TYPE_REQUIRED')
    try:
        registry,source=snapshot();r.validate_active_registry(registry)
        return {**r.classify(payload['requestType'],registry),'workflowSource':source,'selectionProvider':'workflow-decision-service'}
    except (ValueError,KeyError,TypeError)as exc:raise SelectionError(str(exc))from exc
