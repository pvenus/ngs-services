from __future__ import annotations

from copy import deepcopy
import json,hashlib
from typing import Any
from uuid import uuid4

from .errors import ConflictError, NotFoundError, ValidationError
from .repository import WorkflowRepository
from .workflows import workflow_definition


class ExecutionService:
    def __init__(self, repository: WorkflowRepository):
        self.repository = repository

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = self._required(payload, "requestId")
        workflow_type = self._required(payload, "workflowType")
        goal = self._required(payload, "goal")
        inputs = payload.get("inputs", {})
        if not isinstance(inputs, dict):
            raise ValidationError("inputs must be an object")
        try:
            definition = workflow_definition(workflow_type)
        except KeyError as exc:
            raise ValidationError("unsupported workflowType") from exc
        session_id = payload.get("sessionId") or f"exec.{uuid4()}"
        steps = [{**step, "status": "pending"} for step in definition["steps"]]
        steps[0]["status"] = "ready"
        session = {
            "id": session_id, "workflowType": workflow_type, "goal": goal,
            "status": "active", "inputs": deepcopy(inputs), "steps": steps,
            "artifacts": {}, "currentStepId": steps[0]["id"],
        }
        stored, revision = self.repository.put_session(
            session_id, session, expected_revision=0, request_id=request_id, reason="create execution session"
        )
        return self._response(stored, revision)

    def get(self, session_id: str) -> dict[str, Any]:
        resource, revision = self._load(session_id)
        return self._response(resource, revision)

    def advance(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = self._required(payload, "requestId")
        expected = payload.get("expectedRevision")
        if type(expected) is not int or expected < 1:
            raise ValidationError("expectedRevision must be a positive integer")
        session, revision = self._load(session_id)
        event = payload.get("event")
        if not isinstance(event, dict):
            raise ValidationError("event must be an object")
        fingerprint = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        last_request = session.get("lastAdvanceRequest")
        if isinstance(last_request, dict) and last_request.get("requestId") == request_id:
            if last_request.get("fingerprint") != fingerprint:
                raise ValidationError("requestId was reused with a different event")
            return self._response(session, revision)
        if revision != expected:
            raise ConflictError("session revision conflict")
        updated = deepcopy(session)
        self._apply_event(updated, event)
        updated["lastAdvanceRequest"] = {"requestId": request_id, "fingerprint": fingerprint}
        stored, next_revision = self.repository.put_session(
            session_id, updated, expected_revision=revision, request_id=request_id, reason="advance workflow step"
        )
        return self._response(stored, next_revision)

    def _apply_event(self, session: dict, event: dict) -> None:
        event_type = self._required(event, "type")
        current = self._current(session)
        if event_type == 'episode_visual_registered':
            if set(event)!={'type','stepId','results'}or session.get('workflowType')!='episode-visual-asset.v1'or session.get('status')!='active'or current.get('phase')!='worker'or event['stepId']!=current['id']:raise ValidationError('visual worker result phase denied')
            values=event['results'];providers=session['inputs']['visualReceipt']['providerRequests']
            if not isinstance(values,list)or len(values)!=(1 if session['inputs']['visualReceipt'].get('requestKind')=='ui-mockup-correction'else 3) or [v.get('index')for v in values]!=list(range(1 if session['inputs']['visualReceipt'].get('requestKind')=='ui-mockup-correction'else 3)):raise ValidationError('three visual results required')
            for v,p in zip(values,providers):
                h=hashlib.sha256(json.dumps(p['arguments'],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
                if v.get('providerArgumentsHash')!=h or not v.get('fileAssetId')or not v.get('sha256'):raise ValidationError('visual result binding denied')
            session['visualResults']=deepcopy(values);current['output']={'results':deepcopy(values)};current['status']='completed';session['status']='completed';session['currentStepId']=None;return
        if event_type == 'native_trial_claimed':
            claim=event.get('claim');receipt=session['inputs'].get('_nodeWorkflowReceipt',{})
            if set(event)!={'type','stepId','claim'} or session.get('workflowType')!='story-node-image.v1' or session.get('status')!='active' or current.get('phase')!='native' or event['stepId']!=current['id'] or session.get('nativeTrialClaim') or receipt.get('maximumImageCalls')!=1 or not isinstance(claim,dict) or claim.get('providerRequestHash')!=receipt.get('providerRequestHash'):raise ValidationError('native trial claim denied')
            session['nativeTrialClaim']=deepcopy(claim);return
        if event_type == 'native_trial_observed':
            if set(event)!={'type','stepId','result'} or session.get('workflowType')!='story-node-image.v1' or session.get('status')!='active' or current.get('phase')!='native' or event['stepId']!=current['id'] or any(s.get('output') for s in session['steps']):raise ValidationError('native trial observation phase denied')
            result=event['result']
            if not isinstance(result,dict) or result.get('providerRequestHash')!=session['inputs']['_nodeWorkflowReceipt']['providerRequestHash'] or result.get('quality') not in ('passed','failed') or not result.get('libraryFileId'):raise ValidationError('native trial observation binding invalid')
            current['output']=deepcopy(result);current['status']='completed' if result['quality']=='passed' else 'failed'
            session['nativeTrialResult']=deepcopy(result);session['status']=current['status'];session['currentStepId']=None
            return
        if event_type == 'node_inputs_revised':
            if session.get('nativeTrialClaim') or session['inputs'].get('_singleShotRequestHash'):raise ValidationError('claimed or compact inputs are immutable')
            if set(event)!={'type','stepId','previousInputsHash','inputs','transitionHash'} or session.get('workflowType')!='story-node-image.v1' or session.get('status')!='active' or current.get('phase')!='native' or event['stepId']!=current['id'] or any(s.get('output') for s in session['steps']):raise ValidationError('node input revision phase denied')
            def digest(v):return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            old=session['inputs'];new=event['inputs']
            if digest(old)!=event['previousInputsHash'] or not isinstance(new,dict) or new.get('nodeImage',{}).get('target')!=old['nodeImage']['target'] or new.get('nodeImage',{}).get('requestMessageHash')!=old['nodeImage']['requestMessageHash'] or not new.get('_nodeSourceReview') or new.get('_nodeWorkflowReceipt',{}).get('sourceReviewHash')!=digest(new['_nodeSourceReview']) or new['_nodeWorkflowReceipt'].get('providerRequestHash')!=digest(new.get('_nativeImageArguments')):raise ValidationError('node input revision binding invalid')
            history=session.setdefault('nodeInputHistory',[])
            history.append({'inputVersion':session.get('nodeInputVersion',1),'inputs':deepcopy(old),'inputsHash':digest(old),'supersededByTransitionHash':event['transitionHash']})
            session['inputs']=deepcopy(new);session['nodeInputVersion']=len(history)+1;session['nodeInputTransitionHash']=event['transitionHash']
            return
        if event_type == "input_supplied":
            if session.get("workflowType")=="story-node-image.v1":raise ValidationError("node inputs require reviewed revision")
            values = event.get("values")
            if not isinstance(values, dict) or not values:
                raise ValidationError("input_supplied.values must be a non-empty object")
            session["inputs"].update(values)
            return
        if event.get("stepId") != current["id"]:
            raise ValidationError("event stepId is not the current step")
        if event_type == "tool_result":
            if session["inputs"].get("_singleShotRequestHash"):raise ValidationError("compact trial requires observed quality result")
            if current["phase"] not in {"get", "execute", "native"}:
                raise ValidationError("tool_result is not valid for this phase")
            output = event.get("output")
            if not isinstance(output, dict):
                raise ValidationError("tool_result.output must be an object")
            current["output"] = deepcopy(output)
            session["inputs"].update({k: v for k, v in output.items() if k in {"sourceAssetId", "resultAssetId"}})
            self._complete_and_move(session, current)
            return
        if event_type == "artifact_submitted":
            if current["phase"] != "compose":
                raise ValidationError("artifact_submitted is only valid for compose")
            artifact = event.get("artifact")
            if not isinstance(artifact, dict):
                raise ValidationError("artifact must be an object")
            missing = [key for key in current["submissionRequired"] if artifact.get(key) in (None, "")]
            if missing:
                raise ValidationError("artifact is missing required fields: " + ", ".join(missing))
            artifact_id = event.get("artifactId") or f"artifact.{uuid4()}"
            session["artifacts"][artifact_id] = {
                "id": artifact_id, "type": current["artifactType"], "content": deepcopy(artifact), "status": "accepted"
            }
            current["artifactId"] = artifact_id
            self._complete_and_move(session, current)
            return
        if event_type == "validation_result":
            if current["phase"] != "validate":
                raise ValidationError("validation_result is only valid for validate")
            passed = event.get("passed")
            if type(passed) is not bool:
                raise ValidationError("validation_result.passed must be boolean")
            current["validation"] = {"passed": passed, "summary": event.get("summary", "")}
            current["status"] = "completed" if passed else "failed"
            session["status"] = "completed" if passed else "failed"
            session["currentStepId"] = None
            return
        raise ValidationError("unsupported event type")

    @staticmethod
    def _complete_and_move(session, current):
        current["status"] = "completed"
        index = session["steps"].index(current)
        if index + 1 >= len(session["steps"]):
            session["status"] = "completed"
            session["currentStepId"] = None
            return
        following = session["steps"][index + 1]
        following["status"] = "ready"
        session["currentStepId"] = following["id"]

    def _response(self, session, revision):
        return {"session": deepcopy(session), "revision": revision, "nextAction": self._next_action(session)}

    def _next_action(self, session):
        if session["status"] in {"completed", "failed"}:
            return {"type": "completed", "status": session["status"]}
        step = self._current(session)
        if session.get('workflowType')=='episode-visual-asset.v1':return {'type':'await_external_result','stepId':step['id'],'workerOwnedExecution':True,'maximumImageCalls':1 if session['inputs']['visualReceipt'].get('requestKind')=='ui-mockup-correction'else 3}
        missing = [name for name in step.get("requiredInputs", []) if session["inputs"].get(name) in (None, "")]
        if missing:
            return {
                "type": "collect_input", "stepId": step["id"],
                "slots": [{"name": name, "source": "codex_or_user", "required": True} for name in missing],
            }
        if step["phase"] == "native":
            if session.get("nativeTrialClaim"):return {"type":"await_external_result","stepId":step["id"],"providerRequestHash":session["nativeTrialClaim"]["providerRequestHash"],"mustNotRegenerate":True}
            return {"type":"execute_tool","stepId":step["id"],"tool":step["operation"],"permission":"controlled_write","parameters":deepcopy(session["inputs"]["_nativeImageArguments"]),"requiresConfirmation":True}
        if step["phase"] == "get":
            return {"type": "execute_tool", "stepId": step["id"], "tool": step["operation"],
                    "permission": "read_only", "parameters": {k: session["inputs"][k] for k in step["requiredInputs"]}}
        if step["phase"] == "compose":
            return {"type": "compose_artifact", "stepId": step["id"], "templateId": step["templateId"],
                    "artifactType": step["artifactType"],
                    "inputs": {k: session["inputs"][k] for k in step["requiredInputs"]}}
        if step["phase"] == "execute":
            artifact = next((v for v in session["artifacts"].values() if v["type"] in step["requiredArtifacts"]), None)
            if artifact is None:
                raise ValidationError("required artifact is missing")
            return {"type": "execute_tool", "stepId": step["id"], "tool": step["operation"],
                    "permission": step["permission"], "parameters": {"processingSpecArtifactId": artifact["id"]},
                    "requiresConfirmation": True}
        return {"type": "validate_result", "stepId": step["id"], "tool": step["operation"],
                "parameters": {k: session["inputs"][k] for k in step["requiredInputs"]}}

    def _load(self, session_id):
        value = self.repository.get_session(session_id)
        if value is None:
            raise NotFoundError("execution session not found")
        return value

    @staticmethod
    def _current(session):
        current_id = session.get("currentStepId")
        for step in session["steps"]:
            if step["id"] == current_id:
                return step
        raise ValidationError("session has no current step")

    @staticmethod
    def _required(payload, name):
        value = payload.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"{name} is required")
        return value.strip()
