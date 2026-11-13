from __future__ import annotations
import os

from copy import deepcopy
import hashlib,json,re
from typing import Any
from uuid import uuid4

from .client import ServiceClient, UpstreamError


class WorkflowError(ValueError):
    pass


class WorkflowService:
    """One-step-at-a-time orchestration boundary for Codex and backend services."""

    def __init__(self, *, laya: ServiceClient, catalog: ServiceClient, execution: ServiceClient,
                 files: ServiceClient, video_alpha: ServiceClient, data: ServiceClient | None = None, portrait_cache=None):
        self.laya, self.catalog, self.execution = laya, catalog, execution
        self.files, self.video_alpha = files, video_alpha
        self.data = data
        self.portrait_cache = portrait_cache

    def list_runs(self, status: str | None = None, limit: int = 100) -> dict[str, Any]:
        if self.data is None:
            raise WorkflowError("workflow tracking is unavailable")
        query = f"?limit={limit}" + (f"&status={status}" if status else "")
        return self.data.request("GET", "/v1/workflows/sessions" + query)

    def trace(self, session_id: str) -> dict[str, Any]:
        if self.data is None:
            raise WorkflowError("workflow tracking is unavailable")
        encoded = self.data.ident(session_id)
        session = self.data.request("GET", "/v1/workflows/sessions/" + encoded)
        events = self.data.request("GET", "/v1/workflows/sessions/" + encoded + "/events")
        related = []
        job_id = session.get("resource", {}).get("inputs", {}).get("jobId")
        if isinstance(job_id, str) and job_id and job_id != session_id:
            job = self.data.request("GET", "/v1/workflows/sessions/" + self.data.ident(job_id), allow_not_found=True)
            if job:
                job_events = self.data.request("GET", "/v1/workflows/sessions/" + self.data.ident(job_id) + "/events")
                related.append({"session": job, "events": job_events.get("items", [])})
        return {"session": session, "events": events.get("items", []), "relatedRuns": related}

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        intent = self._text(payload, "intent")
        if "episodeVisualRequest" in payload:
            from .episode_visual import start
            return start(self,payload,intent)
        if "nodeSingleShotRequest" in payload:return self._start_single_shot(payload,intent)
        if "nodePortraitShotRequest" in payload:return self._start_portrait_scene(payload,intent)
        inputs = payload.get("inputs", {})
        if not isinstance(inputs, dict):
            raise WorkflowError("inputs must be an object")
        clean = self._public_inputs(inputs)
        if clean.get('nodeImage') is not None:return self._start_node(payload,intent,clean)
        clean.setdefault("jobId", "video-alpha." + uuid4().hex)
        selection = self.laya.request("POST", "/v1/selections", {"intent": intent, "inputs": clean})
        if selection.get("serviceId") != "video-alpha" or selection.get("actionId") != "video-alpha.compose-spec":
            raise WorkflowError("selected workflow is not supported")
        selected_context = self.catalog.request("GET", "/v1/catalog/execution-context/" +
                                                self.catalog.ident(selection["actionId"]))
        session_id = payload.get("sessionId") or "workflow." + uuid4().hex
        created = self.execution.request("POST", "/v1/executions", {
            "requestId": payload.get("requestId") or "workflow-start." + uuid4().hex,
            "sessionId": session_id,
            "workflowType": "video-alpha.v1",
            "goal": intent,
            "inputs": clean,
        })
        return self._view(created, selection=selection, codex_context=self._codex_context(selected_context))

    def get(self, session_id: str) -> dict[str, Any]:
        return self._view(self._execution(session_id))

    def submit(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = self._execution(session_id)
        action = current["nextAction"]
        if "episodeVisualResults" in payload:
            from .episode_visual import results
            return results(self,session_id,current,payload)
        if "nativeTrialClaim" in payload:return self._claim_node_trial(session_id,current,payload)
        if "nodePortraitReferencePreview" in payload:return self._preview_portrait_scene(session_id,current,payload)
        if "nodePromptPreview" in payload:return self._preview_node_prompt(session_id,current,payload)
        if "nodeInputRevision" in payload:return self._revise_node(session_id,current,payload)
        if 'nativeTrialResult' in payload:return self._observe_node_trial(session_id,current,payload)
        request_id = payload.get("requestId") or "workflow-submit." + uuid4().hex
        if current['session']['workflowType']=='story-node-image.v1' and action.get('tool')=='image_gen.imagegen':
            result=payload.get('nativeResult');binding=current['session']['inputs']['nodeImage']
            if not isinstance(result,dict) or set(result)!={'resultAssetId','sha256','providerRequestHash'} or result['providerRequestHash']!=self._hash(action['parameters']):raise WorkflowError('NODE_IMAGE_NATIVE_RESULT_BINDING_INVALID')
            asset=self.files.request('GET','/v1/assets/'+self.files.ident(result['resultAssetId']))
            if asset.get('id')!=result['resultAssetId'] or asset.get('status')!='available' or asset.get('sha256')!=result['sha256'] or asset.get('projectId')!=current['session']['inputs']['projectId']:raise WorkflowError('NODE_IMAGE_NATIVE_ASSET_BINDING_INVALID')
            advanced=self._advance(session_id,current,request_id,{'type':'tool_result','stepId':action['stepId'],'output':{'resultAssetId':result['resultAssetId'],'sha256':result['sha256']}})
            return self._view(advanced)
        if action["type"] == "collect_input":
            values = payload.get("values")
            if not isinstance(values, dict) or not values:
                raise WorkflowError("values are required for collect_input")
            allowed = {slot["name"] for slot in action.get("slots", [])}
            clean = self._public_inputs(values)
            if set(clean) - allowed:
                raise WorkflowError("submitted values are not requested by the current step")
            event = {"type": "input_supplied", "values": clean}
        elif action["type"] == "compose_artifact":
            artifact = payload.get("artifact")
            if not isinstance(artifact, dict):
                raise WorkflowError("artifact is required for compose_artifact")
            public = self._processing_artifact(artifact, current)
            event = {"type": "artifact_submitted", "stepId": action["stepId"],
                     "artifactId": payload.get("artifactId") or "artifact." + uuid4().hex,
                     "artifact": public}
        else:
            raise WorkflowError("current step does not accept Codex submission")
        advanced = self._advance(session_id, current, request_id, event)
        return self._view(advanced)

    def execute_next(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = self._execution(session_id)
        action = current["nextAction"]
        request_id = payload.get("requestId") or "workflow-next." + uuid4().hex
        if action["type"] == "await_external_result":return self._view(current)
        if action["type"] == "collect_input" or action["type"] == "compose_artifact":
            return self._view(current)
        if current['session']['workflowType']=='story-node-image.v1' and action.get('tool')=='image_gen.imagegen':
            # Codex owns native tool execution; the service never generates/retries.
            return self._view(current)
        if action["type"] == "execute_tool" and action["tool"] == "video_job_get":
            job_id = action["parameters"]["jobId"]
            job = self.video_alpha.request("GET", "/v1/jobs/" + self.video_alpha.ident(job_id), allow_not_found=True)
            output = job or {"id": job_id, "status": "not-created"}
            inputs = current["session"].get("inputs", {})
            if inputs.get("sourceAssetId"):
                output["sourceAssetId"] = inputs["sourceAssetId"]
            advanced = self._advance(session_id, current, request_id,
                                     {"type": "tool_result", "stepId": action["stepId"], "output": output})
            return self._view(advanced)
        if action["type"] == "execute_tool" and action["tool"] == "video-alpha.apply":
            if payload.get("confirmed") is not True:
                raise WorkflowError("confirmed=true is required for external execution")
            session = current["session"]
            artifact = self._artifact(session, action["parameters"]["processingSpecArtifactId"])
            processing_spec = {key: value for key, value in artifact.items() if key != "sourceAssetId"}
            inputs = session["inputs"]
            project_id = inputs.get("projectId")
            if not isinstance(project_id, str) or not project_id:
                raise WorkflowError("projectId is required before execution")
            job_id = inputs["jobId"]
            job = self.video_alpha.request("GET", "/v1/jobs/" + self.video_alpha.ident(job_id), allow_not_found=True)
            if job is None:
                job = self.video_alpha.request("POST", "/v1/jobs", {
                    "id": job_id, "projectId": project_id,
                    "sourceAssetId": artifact["sourceAssetId"], "processingSpec": processing_spec,
                })
            if job.get("status") == "completed":
                return self._complete_video_step(session_id, current, request_id, job)
            if job.get("status") == "running":
                waiting = self._view(current); waiting["operation"] = {"jobId": job_id, "status": "running"}
                return waiting
            accepted = self.video_alpha.request("POST", "/v1/jobs/" + self.video_alpha.ident(job_id) + "/run-async", {
                "observedRevision": job["revision"], "confirmed": True, "processingSpec": processing_spec,
            })
            waiting = self._view(current); waiting["operation"] = {"jobId": job_id, "status": accepted.get("status", "accepted")}
            return waiting
        if action["type"] == "validate_result":
            asset_id = action["parameters"]["resultAssetId"]
            asset = self.files.request("GET", "/v1/assets/" + self.files.ident(asset_id), allow_not_found=True)
            advanced = self._advance(session_id, current, request_id, {"type": "validation_result",
                "stepId": action["stepId"], "passed": bool(asset and asset.get("status") == "available"),
                "summary": "result asset is available" if asset else "result asset was not found"})
            return self._view(advanced)
        if action["type"] == "completed":
            return self._view(current)
        raise WorkflowError("unsupported next action")

    def _complete_video_step(self, session_id, current, request_id, completed):
        result = completed.get("result") or {}
        result_asset_id = result.get("resultFramesAssetId")
        if not result_asset_id: raise WorkflowError("video service returned no result asset")
        advanced = self._advance(session_id, current, request_id, {"type": "tool_result",
            "stepId": current["nextAction"]["stepId"], "output": {"resultAssetId": result_asset_id,
            "manifestAssetId": result.get("manifestAssetId"), "jobId": completed["id"]}})
        return self._view(advanced)

    def result(self, session_id: str) -> dict[str, Any]:
        current = self._execution(session_id)
        session = current["session"]
        if session["status"] not in {"completed", "failed"}:
            raise WorkflowError("workflow is not completed")
        outputs = {}
        if session.get("nativeTrialResult"):outputs.update(deepcopy(session["nativeTrialResult"]))
        for step in session.get("steps", []):
            if isinstance(step.get("output"), dict):
                outputs.update({key: value for key, value in step["output"].items()
                                if key.endswith("AssetId") or key == "jobId"})
        return {"sessionId": session["id"], "status": session["status"], "result": outputs}

    def _execution(self, session_id: str):
        return self.execution.request("GET", "/v1/executions/" + self.execution.ident(session_id))

    def _advance(self, session_id, current, request_id, event):
        return self.execution.request("POST", "/v1/executions/" + self.execution.ident(session_id) + "/advance", {
            "requestId": request_id, "expectedRevision": current["revision"], "event": event,
        })

    @staticmethod
    def _artifact(session, artifact_id):
        value = session.get("artifacts", {}).get(artifact_id, {}).get("content")
        if not isinstance(value, dict):
            raise WorkflowError("processing artifact was not found")
        return deepcopy(value)

    @staticmethod
    def _public_inputs(values):
        allowed = {"projectId", "jobId", "sourceAssetId", "alphaMethod", "outputFormat",
                   "quality", "temporalStability", "outputKinds", "prompt", "nodeImage"}
        unsupported = set(values) - allowed
        if unsupported:
            raise WorkflowError("unsupported public inputs: " + ", ".join(sorted(unsupported)))
        return deepcopy(values)

    @staticmethod
    def _processing_artifact(artifact, current):
        allowed = {"sourceAssetId", "alphaMethod", "outputFormat"}
        if set(artifact) - allowed:
            raise WorkflowError("processing artifact contains internal or unsupported fields")
        merged = {**{key: value for key, value in current["session"]["inputs"].items() if key in allowed}, **artifact}
        missing = [key for key in allowed if merged.get(key) in (None, "")]
        if missing:
            raise WorkflowError("processing artifact is missing: " + ", ".join(sorted(missing)))
        if merged["alphaMethod"] not in {"model-matting", "chroma-key"} or merged["outputFormat"] != "rgba-png":
            raise WorkflowError("unsupported processing option")
        return merged

    @staticmethod
    def _text(payload, name):
        value = payload.get(name)
        if not isinstance(value, str) or not value.strip():
            raise WorkflowError(f"{name} is required")
        return value.strip()

    @staticmethod
    def _hash(value):
        return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

    def _start_node(self,payload,intent,clean):
        self._validate_node_inputs(clean)
        sid=payload.get('sessionId')
        if sid:
            existing=self.execution.request('GET','/v1/executions/'+self.execution.ident(sid),allow_not_found=True)
            if existing:
                prior={k:v for k,v in existing['session']['inputs'].items() if not k.startswith('_')}
                if prior!=clean or existing['session'].get('workflowType')!='story-node-image.v1':raise WorkflowError('NODE_IMAGE_SESSION_BINDING_CONFLICT')
                return self._view(existing)
        raise WorkflowError('NODE_IMAGE_MAIN_PORTRAIT_REFERENCES_REQUIRED')

    def _validate_node_inputs(self,clean):
        if set(clean)!={'projectId','prompt','outputKinds','nodeImage'} or clean['projectId']!='binari' or clean['outputKinds']!=['image'] or not isinstance(clean['prompt'],str) or not 5<=len(clean['prompt'])<=20000:raise WorkflowError('NODE_IMAGE_PUBLIC_INPUT_INVALID')
        binding=clean['nodeImage'];target=binding.get('target',{}) if isinstance(binding,dict) else {}
        if not isinstance(binding,dict) or set(binding)!={'target','sourceBindings','inputHash','requestMessageHash','relatedNpcIds'} or set(target)!={'projectId','actId','chapterId','episodeId','nodeId'} or target.get('projectId')!=clean['projectId'] or any(not isinstance(v,str) or not re.fullmatch('[A-Za-z0-9_.:-]{1,256}',v) for v in target.values()):raise WorkflowError('NODE_IMAGE_TARGET_BINDING_INVALID')
        for key in ('inputHash','requestMessageHash'):
            if not isinstance(binding[key],str) or not re.fullmatch('[a-f0-9]{64}',binding[key]):raise WorkflowError('NODE_IMAGE_INPUT_HASH_REQUIRED')
        if not isinstance(binding['relatedNpcIds'],list) or not 1<=len(binding['relatedNpcIds'])<=8 or len(set(binding['relatedNpcIds']))!=len(binding['relatedNpcIds']) or any(not isinstance(v,str) or not re.fullmatch('[A-Za-z0-9_.:-]{1,256}',v) for v in binding['relatedNpcIds']):raise WorkflowError('NODE_IMAGE_CHARACTER_BINDINGS_REQUIRED')
        if set(binding['sourceBindings'])!={'story','masterConcept','npcs'}:raise WorkflowError('NODE_IMAGE_SOURCE_BINDINGS_REQUIRED')
        for source in binding['sourceBindings'].values():
            if set(source)!={'revision','documentHash','envelopeHash'} or type(source['revision']) is not int or source['revision']<0 or any(not isinstance(source[k],str) or not re.fullmatch('[a-f0-9]{64}',source[k]) for k in ('documentHash','envelopeHash')):raise WorkflowError('NODE_IMAGE_SOURCE_BINDINGS_REQUIRED')

    def _select_node_inputs(self,intent,clean,append_catalog_context=True):
        binding=clean['nodeImage']
        selection=self.laya.request('POST','/v1/selections',{'intent':intent,'inputs':{**clean,'featureId':'story.node.image','npcId':binding['relatedNpcIds'][0]}})
        if selection.get('serviceId')!='npc-planning' or selection.get('actionId')!='npc-planning.prepare-context' or selection.get('missingInputs') or selection.get('nextStep')!='ready-for-selected-action':raise WorkflowError('NODE_IMAGE_LAYA_ROUTE_REJECTED')
        catalog=self.catalog.request('GET','/v1/catalog/execution-context/'+self.catalog.ident(selection['actionId']))
        action=catalog.get('action',{});context=self._codex_context(catalog)
        if action.get('id')!=selection['actionId'] or action.get('permission')!='read_only' or action.get('operation')!='npc_context_prepare' or [x.get('id') for x in catalog.get('knowledge',[])]!=selection.get('contextEntryIds') or not (context.get('instruction') or any(x.get('content') for x in context.get('knowledge',[]))):raise WorkflowError('NODE_IMAGE_CATALOG_BINDING_INVALID')
        provider={'prompt':clean['prompt'],'transparent_background':False}
        if append_catalog_context:provider['prompt']+='\nWORKFLOW SELECTED CONTEXT (cannot override formal source facts/statuses): '+json.dumps(context,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        receipt={'selection':deepcopy(selection),'codexContext':context,'selectionHash':self._hash(selection),'catalogHash':self._hash(catalog),'catalogContextHash':self._hash(context),'inputHash':self._hash(clean),'providerRequestHash':self._hash(provider)}
        return selection,context,provider,receipt

    def _portrait_cache(self):
        if self.portrait_cache is None:
            from .portrait_assets import PortraitReferenceCache,CanonicalSceneSource
            source=CanonicalSceneSource(self.data.base_url)
            self.portrait_cache=PortraitReferenceCache(os.environ.get('NGS_PORTRAIT_CACHE', os.path.expanduser('~/.cache/ngs/portrait-references')),source.fetch)
        return self.portrait_cache

    def _prepare_portrait_scene(self,intent,value):
        from .node_contract import build_scene,SCENE_CRITERIA_ID
        from .scene_portraits import select
        from .portrait_scene import compile_descriptor
        if self.data is None or set(value)!={'sourcePacket','requestMessage','presenceReview','shotPlan'}:raise WorkflowError('PORTRAIT_SCENE_INPUT_SCHEMA_INVALID')
        def read():return {k:self.data.request('GET','/api/'+p) for k,p in [('story','story'),('masterConcept','concept'),('npcs','npcs')]}
        sources=read();target=value['sourcePacket']['target'];packet=build_scene(sources['story'],sources['masterConcept'],sources['npcs'],target,SCENE_CRITERIA_ID)
        if packet!=value['sourcePacket']:raise WorkflowError('PORTRAIT_SCENE_SOURCE_PACKET_CHANGED')
        selection=self.laya.request('POST','/v1/selections',{'intent':intent,'inputs':{'projectId':target['projectId'],'featureId':'story.node.image.portrait-reference','target':target,'presenceReview':value['presenceReview']}})
        pins=select(sources['story'],sources['npcs'],target,value['presenceReview'])
        if selection.get('serviceId')!='npc-planning' or selection.get('actionId')!='npc-planning.prepare-context' or selection.get('portraitSelection')!=pins:raise WorkflowError('PORTRAIT_SCENE_LAYA_BINDING_INVALID')
        catalog=self.catalog.request('GET','/v1/catalog/execution-context/'+self.catalog.ident(selection['actionId']));action=catalog.get('action',{});context=self._codex_context(catalog)
        if action.get('id')!=selection['actionId'] or action.get('permission')!='read_only' or action.get('operation')!='npc_context_prepare' or [x.get('id') for x in catalog.get('knowledge',[])]!=selection.get('contextEntryIds'):raise WorkflowError('PORTRAIT_SCENE_CATALOG_BINDING_INVALID')
        cache=self._portrait_cache();manifest=cache.materialize(pins);cache.verify(manifest)
        descriptor=compile_descriptor(packet,value['requestMessage'],value['shotPlan'],pins,manifest)
        if sources!=read():raise WorkflowError('PORTRAIT_SCENE_SOURCE_CHANGED_DURING_PREPARATION')
        return packet,selection,context,catalog,pins,manifest,descriptor

    def _preview_portrait_scene(self,sid,current,payload):
        if set(payload)!={'nodePortraitReferencePreview'} or current['session'].get('workflowType')!='story-node-image.v1':raise WorkflowError('PORTRAIT_SCENE_PREVIEW_SCHEMA_INVALID')
        value=payload['nodePortraitReferencePreview']
        if value.get('sourcePacket',{}).get('target')!=current['session']['inputs']['nodeImage']['target']:raise WorkflowError('PORTRAIT_SCENE_PREVIEW_TARGET_CHANGED')
        prepared=self._prepare_portrait_scene(current['session']['goal'],value)
        return {'schemaVersion':'npc-node-portrait-preview.v1','sourceSessionId':sid,'sourceSessionRevision':current['revision'],'sessionUnchanged':True,'previewOnly':True,'executionAllowed':False,'layaSelection':prepared[1],'referenceManifest':prepared[5],'descriptor':prepared[6]}

    def _start_portrait_scene(self,payload,intent):
        if set(payload)!={'intent','sessionId','nodePortraitShotRequest'}:raise WorkflowError('PORTRAIT_SCENE_REQUEST_SCHEMA_INVALID')
        request=payload['nodePortraitShotRequest']
        required={'approvalId','maximumImageCalls','sourcePacket','requestMessage','presenceReview','shotPlan','expectedProviderArguments','expectedProviderRequestHash'}
        if not isinstance(request,dict) or set(request)!=required or type(request['maximumImageCalls'])is not int or request['maximumImageCalls']!=1 or not isinstance(request['approvalId'],str) or not re.fullmatch('Sentinel_[a-f0-9]+',request['approvalId']):raise WorkflowError('PORTRAIT_SCENE_GENERATION_APPROVAL_REQUIRED')
        sid='workflow.node-single-shot.'+self._hash({'approvalId':request['approvalId']});fingerprint=self._hash(request)
        if sid!=payload['sessionId']:raise WorkflowError('PORTRAIT_SCENE_SESSION_BINDING_INVALID')
        existing=self.execution.request('GET','/v1/executions/'+self.execution.ident(sid),allow_not_found=True)
        if existing:
            if existing['session']['inputs'].get('_singleShotRequestHash')!=fingerprint:raise WorkflowError('PORTRAIT_SCENE_APPROVAL_ALREADY_BOUND')
            return self._view(existing)
        value={k:request[k] for k in ['sourcePacket','requestMessage','presenceReview','shotPlan']};packet,selection,context,catalog,pins,manifest,descriptor=self._prepare_portrait_scene(intent,value)
        if not descriptor['generationReady']:raise WorkflowError('PORTRAIT_SCENE_REFERENCES_BLOCKED:'+','.join(b['code'] for b in descriptor['blockers']))
        args=descriptor['arguments']
        if args!=request['expectedProviderArguments'] or self._hash(args)!=request['expectedProviderRequestHash']:raise WorkflowError('PORTRAIT_SCENE_ACTUAL_ARGUMENTS_MISMATCH')
        clean={'projectId':packet['target']['projectId'],'prompt':args['prompt'],'outputKinds':['image'],'nodeImage':{'target':packet['target'],'sourceBindings':packet['sourceBindings'],'inputHash':self._hash(packet),'requestMessageHash':self._hash(request['requestMessage']),'relatedNpcIds':[r['npcId'] for r in manifest['references']]}}
        self._validate_node_inputs(clean)
        receipt={'selection':selection,'codexContext':context,'selectionHash':self._hash(selection),'catalogHash':self._hash(catalog),'catalogContextHash':self._hash(context),'inputHash':self._hash(clean),'providerRequestHash':self._hash(args),'compilerVersion':descriptor['compilerVersion'],'renderMetadataHash':descriptor['metadataHash'],'maximumImageCalls':1,'approvalId':request['approvalId'],'portraitSelectionHash':pins['selectionHash'],'portraitManifestHash':manifest['manifestHash']}
        created=self.execution.request('POST','/v1/executions',{'requestId':'portrait-scene-start.'+fingerprint,'sessionId':sid,'workflowType':'story-node-image.v1','goal':intent,'inputs':{**clean,'_nodeWorkflowReceipt':receipt,'_nativeImageArguments':args,'_nodeRenderMetadata':descriptor['metadata'],'_nodePortraitSelection':pins,'_nodeReferenceManifest':manifest,'_nodePresenceReview':request['presenceReview'],'_nodePortraitShotPlan':request['shotPlan'],'_singleShotRequestHash':fingerprint}})
        return self._view(created)

    def _start_single_shot(self,payload,intent):
        if set(payload)!={'intent','sessionId','nodeSingleShotRequest'}:raise WorkflowError('NODE_SHOT_REQUEST_SCHEMA_INVALID')
        request=payload['nodeSingleShotRequest']
        if not isinstance(request,dict) or set(request)!={'approvalId','maximumImageCalls','sourcePacket','requestMessage','singleShotPlan','reviewDecision','expectedProviderArguments','expectedProviderRequestHash'} or request['maximumImageCalls']!=1 or type(request['maximumImageCalls'])is not int or not isinstance(request['approvalId'],str) or not re.fullmatch('Sentinel_[a-f0-9]+',request['approvalId']):raise WorkflowError('NODE_SHOT_APPROVAL_SCOPE_REQUIRED')
        fingerprint=self._hash(request);sid='workflow.node-single-shot.'+self._hash({'approvalId':request['approvalId']})
        if payload['sessionId']!=sid:raise WorkflowError('NODE_SHOT_NEW_SESSION_BINDING_INVALID')
        existing=self.execution.request('GET','/v1/executions/'+self.execution.ident(sid),allow_not_found=True)
        if existing:
            if existing['session']['inputs'].get('_singleShotRequestHash')!=fingerprint or existing['session'].get('goal')!=intent:raise WorkflowError('NODE_SHOT_REQUEST_REUSE_CONFLICT')
            return self._view(existing)
        raise WorkflowError('NODE_IMAGE_MAIN_PORTRAIT_REFERENCES_REQUIRED')

    def _claim_node_trial(self,session_id,current,payload):
        if set(payload)!={'nativeTrialClaim'}:raise WorkflowError('NODE_SHOT_CLAIM_SCHEMA_INVALID')
        claim=payload['nativeTrialClaim'];session=current['session'];receipt=session['inputs'].get('_nodeWorkflowReceipt',{})
        if not isinstance(claim,dict) or set(claim)!={'invocationId','providerRequestHash'} or not isinstance(claim['invocationId'],str) or not re.fullmatch('[A-Za-z0-9_.:-]{1,256}',claim['invocationId']) or claim['providerRequestHash']!=receipt.get('providerRequestHash'):raise WorkflowError('NODE_SHOT_CLAIM_BINDING_INVALID')
        if receipt.get('compilerVersion') not in ('npc-node-single-shot-prompt.v1','npc-node-main-portrait-prompt.v1') or receipt.get('maximumImageCalls')!=1:raise WorkflowError('NODE_SHOT_CLAIM_SCOPE_REQUIRED')
        if session.get('nativeTrialClaim') or session.get('nativeTrialResult'):
            view=self._view(current);view['nativeClaim']={'mayInvokeOnce':False,'mustNotRegenerate':True};return view
        if receipt.get('compilerVersion')!='npc-node-main-portrait-prompt.v1':raise WorkflowError('NODE_IMAGE_MAIN_PORTRAIT_REFERENCES_REQUIRED')
        if session.get('status')!='active' or current['nextAction'].get('tool')!='image_gen.imagegen':raise WorkflowError('NODE_SHOT_CLAIM_PHASE_DENIED')
        if self.data is None:raise WorkflowError('NODE_SHOT_SOURCE_READER_REQUIRED')
        for key,path in [('story','story'),('masterConcept','concept'),('npcs','npcs')]:
            source=self.data.request('GET','/api/'+path);expected=session['inputs']['nodeImage']['sourceBindings'][key]
            if {'revision':source['revision'],'documentHash':self._hash(source['document']),'envelopeHash':self._hash(source)}!=expected:raise WorkflowError('NODE_SHOT_SOURCE_CHANGED_BEFORE_CLAIM')
        if session['inputs'].get('_nodePortraitSelection'):
            value={'sourcePacket':session['inputs']['_nodeRenderMetadata']['sourcePacket'] if 'sourcePacket' in session['inputs']['_nodeRenderMetadata'] else None,'requestMessage':session['inputs']['_nodeRenderMetadata']['requestMessage'],'presenceReview':session['inputs']['_nodePresenceReview'],'shotPlan':session['inputs']['_nodePortraitShotPlan']}
            if value['sourcePacket'] is None:
                from .node_contract import build_scene,SCENE_CRITERIA_ID
                sources={k:self.data.request('GET','/api/'+p) for k,p in [('story','story'),('masterConcept','concept'),('npcs','npcs')]}
                value['sourcePacket']=build_scene(sources['story'],sources['masterConcept'],sources['npcs'],session['inputs']['nodeImage']['target'],SCENE_CRITERIA_ID)
            prepared=self._prepare_portrait_scene(session['goal'],value)
            if prepared[4]!=session['inputs']['_nodePortraitSelection'] or prepared[5]!=session['inputs']['_nodeReferenceManifest'] or prepared[6].get('arguments')!=session['inputs']['_nativeImageArguments']:raise WorkflowError('PORTRAIT_SCENE_REFERENCE_CHANGED_BEFORE_CLAIM')
        action=deepcopy(current['nextAction'])
        advanced=self._advance(session_id,current,'native-trial-claim.'+self._hash(claim),{'type':'native_trial_claimed','stepId':'native-image-handoff','claim':claim})
        view=self._view(advanced);view['nextAction']=action;view['nativeClaim']={**deepcopy(claim),'mayInvokeOnce':True,'mustNotRegenerate':False};return view

    def _preview_node_prompt(self,session_id,current,payload):
        """Source-checked preview only: no session transition or execution grant."""
        from .node_contract import build_scene,SCENE_CRITERIA_ID
        from .node_single_shot import compile_descriptor
        if set(payload)!={'nodePromptPreview'}:raise WorkflowError('NODE_SHOT_PREVIEW_SCHEMA_INVALID')
        value=payload['nodePromptPreview']
        if not isinstance(value,dict) or set(value)!={'sourcePacket','requestMessage','singleShotPlan','reviewDecision'}:raise WorkflowError('NODE_SHOT_PREVIEW_SCHEMA_INVALID')
        session=current['session'];inputs=session['inputs']
        if session.get('workflowType')!='story-node-image.v1' or self.data is None:raise WorkflowError('NODE_SHOT_PREVIEW_SOURCE_ROUTE_REQUIRED')
        def read():return {k:self.data.request('GET','/api/'+p) for k,p in [('story','story'),('masterConcept','concept'),('npcs','npcs')]}
        sources=read();packet=build_scene(sources['story'],sources['masterConcept'],sources['npcs'],inputs['nodeImage']['target'],SCENE_CRITERIA_ID)
        if packet!=value['sourcePacket']:raise WorkflowError('NODE_SHOT_PREVIEW_SOURCE_CHANGED')
        if self._hash(value['requestMessage'])!=inputs['nodeImage']['requestMessageHash']:raise WorkflowError('NODE_SHOT_PREVIEW_MESSAGE_SCOPE_CHANGED')
        receipt=inputs['_nodeWorkflowReceipt'];selection=receipt['selection']
        catalog=self.catalog.request('GET','/v1/catalog/execution-context/'+self.catalog.ident(selection['actionId']))
        if self._hash(catalog)!=receipt['catalogHash']:raise WorkflowError('NODE_SHOT_PREVIEW_CATALOG_CHANGED')
        context=self._codex_context(catalog)
        descriptor=compile_descriptor(packet,value['requestMessage'],value['singleShotPlan'],value['reviewDecision'],selection,context)
        if sources!=read():raise WorkflowError('NODE_SHOT_PREVIEW_SOURCE_CHANGED_DURING_READ')
        return {'schemaVersion':'npc-node-single-shot-preview.v1','sourceSessionId':session_id,'sourceSessionRevision':current['revision'],'sourceSessionStatus':session['status'],'previewOnly':True,'executionAllowed':False,'sessionUnchanged':True,'descriptor':descriptor}

    def _observe_node_trial(self,session_id,current,payload):
        if set(payload)!={'nativeTrialResult'}:raise WorkflowError('NODE_IMAGE_TRIAL_SCHEMA_INVALID')
        result=payload['nativeTrialResult'];session=current['session']
        if session['inputs'].get('_singleShotRequestHash') and not session.get('nativeTrialClaim'):raise WorkflowError('NODE_SHOT_CLAIM_REQUIRED')
        if session.get('nativeTrialResult')==result:return self._view(current)
        if session.get('workflowType')!='story-node-image.v1' or session.get('status')!='active' or session.get('currentStepId')!='native-image-handoff':raise WorkflowError('NODE_IMAGE_TRIAL_ALREADY_CONSUMED_OR_PHASE_DENIED')
        if not isinstance(result,dict) or set(result)!={'providerRequestHash','sha256','rgbaPixelHash','width','height','libraryFileId','quality','findings'} or any(not isinstance(result[k],str) or not re.fullmatch('[a-f0-9]{64}',result[k]) for k in ['providerRequestHash','sha256','rgbaPixelHash']) or result['providerRequestHash']!=session['inputs']['_nodeWorkflowReceipt']['providerRequestHash'] or any(type(result[k]) is not int or not 1<=result[k]<=16384 for k in ['width','height']) or not isinstance(result['libraryFileId'],str) or not re.fullmatch('libfile_[a-f0-9]+',result['libraryFileId']) or result['quality'] not in ('passed','failed') or not isinstance(result['findings'],list) or not 1<=len(result['findings'])<=20 or any(not isinstance(x,str) or not 10<=len(x)<=2000 for x in result['findings']):raise WorkflowError('NODE_IMAGE_TRIAL_BINDING_INVALID')
        return self._view(self._advance(session_id,current,'node-trial-observed.'+self._hash(result),{'type':'native_trial_observed','stepId':'native-image-handoff','result':result}))

    def _revise_node(self,session_id,current,payload):
        from .node_contract import build_scene,SCENE_CRITERIA_ID,compile_descriptor,validate_decision
        if set(payload)!={'nodeInputRevision'}:raise WorkflowError('NODE_IMAGE_REVISION_SCHEMA_INVALID')
        revision=payload['nodeInputRevision']
        if not isinstance(revision,dict) or set(revision)!={'expectedRevision','previousProviderRequestHash','sourcePacket','requestMessage','reviewDecision'}:raise WorkflowError('NODE_IMAGE_REVISION_SCHEMA_INVALID')
        session=current['session'];inputs=session['inputs'];prior=inputs.get('_nodeWorkflowReceipt',{})
        transition_hash=self._hash(revision)
        # A lost response is recovered without reselecting, appending or retrying generation.
        if session.get('nodeInputTransitionHash')==transition_hash:return self._view(current)
        if not session['inputs'].get('_nodePortraitSelection'):raise WorkflowError('NODE_IMAGE_MAIN_PORTRAIT_REFERENCES_REQUIRED')
        if session.get('workflowType')!='story-node-image.v1' or session.get('status')!='active' or session.get('currentStepId')!='native-image-handoff' or any(s.get('output') for s in session['steps']):raise WorkflowError('NODE_IMAGE_REVISION_PHASE_DENIED')
        if revision['expectedRevision']!=current['revision'] or revision['previousProviderRequestHash']!=prior.get('providerRequestHash'):raise WorkflowError('NODE_IMAGE_REVISION_CONFLICT')
        if self.data is None:raise WorkflowError('NODE_IMAGE_SOURCE_READER_REQUIRED')
        def read():return {k:self.data.request('GET','/api/'+p) for k,p in [('story','story'),('masterConcept','concept'),('npcs','npcs')]}
        sources=read();packet=build_scene(sources['story'],sources['masterConcept'],sources['npcs'],inputs['nodeImage']['target'],SCENE_CRITERIA_ID)
        if packet!=revision['sourcePacket']:raise WorkflowError('NODE_IMAGE_REVISION_SOURCE_PROJECTION_MISMATCH')
        message=revision['requestMessage']
        if not isinstance(message,str) or self._hash(message)!=inputs['nodeImage']['requestMessageHash']:raise WorkflowError('NODE_IMAGE_REVISION_MESSAGE_CHANGED')
        review=validate_decision(packet,revision['reviewDecision'])
        descriptor=compile_descriptor(packet,message,0)
        clean={'projectId':inputs['projectId'],'prompt':descriptor['arguments']['prompt'],'outputKinds':['image'],'nodeImage':{'target':packet['target'],'sourceBindings':packet['sourceBindings'],'inputHash':self._hash(packet),'requestMessageHash':self._hash(message),'relatedNpcIds':[c['data']['id'] for c in packet['characters']]}}
        self._validate_node_inputs(clean)
        selection,context,provider,receipt=self._select_node_inputs(session['goal'],clean)
        provider['prompt']+='\nSOURCE-BOUND NONCANONICAL TRIAL RENDER PLAN: '+json.dumps(review['renderingPlan'],ensure_ascii=False,sort_keys=True,separators=(',',':'))
        receipt.update(providerRequestHash=self._hash(provider),sourceReviewHash=self._hash(review))
        if sources!=read():raise WorkflowError('NODE_IMAGE_SOURCE_CHANGED_DURING_REVIEW')
        advanced=self._advance(session_id,current,'node-input-revision.'+transition_hash,{'type':'node_inputs_revised','stepId':'native-image-handoff','previousInputsHash':self._hash(inputs),'inputs':{**clean,'_nodeWorkflowReceipt':receipt,'_nativeImageArguments':provider,'_nodeSourceReview':review},'transitionHash':transition_hash})
        return self._view(advanced)

    @staticmethod
    def _codex_context(context):
        action, template = context.get("action") or {}, context.get("template") or {}
        knowledge = context.get("knowledge") or []
        return {
            "action": {key: action.get(key) for key in ("id", "name", "permission", "required_inputs")},
            "instruction": template.get("compose_instruction"),
            "requiredInputs": template.get("required_inputs", []),
            "forbiddenActions": template.get("forbidden_actions", []),
            "knowledge": [{"id": item.get("id"), "purpose": item.get("purpose"), "content": item.get("content")}
                          for item in knowledge if isinstance(item, dict)],
        }

    @staticmethod
    def _view(value, selection=None, codex_context=None):
        session, action = value["session"], deepcopy(value["nextAction"])
        result = {"sessionId": session["id"], "status": session["status"],
                  "revision": value["revision"], "nextAction": action}
        if selection:
            result["selection"] = {key: selection.get(key) for key in
                ("serviceId", "actionId", "templateId", "contextEntryIds", "selectionReason")}
        if codex_context is not None:
            result["codexContext"] = codex_context
        if session.get('workflowType')=='episode-visual-asset.v1':
            receipt=session['inputs']['visualReceipt'];result.update(visualReceipt=deepcopy(receipt),selection=deepcopy(receipt['selection']),codexContext=deepcopy(receipt['codexContext']))
        if session.get('nativeTrialClaim'):result['trialClaim']=deepcopy(session['nativeTrialClaim'])
        if session.get('nativeTrialResult'):result['trialObservation']=deepcopy(session['nativeTrialResult'])
        if session.get('workflowType')=='story-node-image.v1':
            inputs=session['inputs'];receipt=inputs['_nodeWorkflowReceipt']
            result.update(selection=deepcopy(receipt['selection']),codexContext=deepcopy(receipt['codexContext']),workflowBinding={'target':deepcopy(inputs['nodeImage']['target']),'sourceBindings':deepcopy(inputs['nodeImage']['sourceBindings']),'inputHash':inputs['nodeImage']['inputHash'],'requestMessageHash':inputs['nodeImage']['requestMessageHash'],'requestInputsHash':WorkflowService._hash({k:v for k,v in inputs.items() if not k.startswith('_')}),'selectionHash':receipt['selectionHash'],'catalogHash':receipt['catalogHash'],'catalogContextHash':receipt['catalogContextHash'],'providerRequestHash':receipt['providerRequestHash']})
            if inputs.get('_nodeRenderMetadata'):
                result['renderContract']={'compilerVersion':receipt['compilerVersion'],'renderMetadataHash':receipt['renderMetadataHash'],'maximumImageCalls':receipt['maximumImageCalls'],'approvalId':receipt['approvalId']}
            if inputs.get('_nodePortraitSelection'):result['portraitReferenceBinding']={'selectionHash':receipt['portraitSelectionHash'],'manifestHash':receipt['portraitManifestHash']}
            if inputs.get('_nodeSourceReview'):
                result['sourceReview']=deepcopy(inputs['_nodeSourceReview'])
                result['workflowBinding'].update(inputVersion=session.get('nodeInputVersion',1),sourceReviewHash=WorkflowService._hash(inputs['_nodeSourceReview']),inputHistoryHash=WorkflowService._hash(session.get('nodeInputHistory',[])))
        return result
