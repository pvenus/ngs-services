"""No knowledge database, model, catalog, subprocess, or execution dependency."""
import importlib.util
import json
import re
from pathlib import Path

_contract_path = Path(__file__).resolve().parents[2] / "contracts" / "workflow_selection.py"
_spec = importlib.util.spec_from_file_location("ngs_workflow_selection", _contract_path)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
SelectionError = _contract.SelectionError

class WorkflowDecisionService:
    def __init__(self, registry_path=None, *, feature_selector=None, one_stop_direction_streaming=False, one_stop_direction_target=None):
        self.feature_selector = feature_selector
        self.one_stop_direction_streaming = one_stop_direction_streaming is True
        if one_stop_direction_target is not None and (
                not self.one_stop_direction_streaming or not isinstance(one_stop_direction_target,str)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,199}',one_stop_direction_target)):
            raise ValueError('ONE_STOP_DIRECTION_TARGET_INVALID')
        self.one_stop_direction_target = one_stop_direction_target
        self.registry_path = Path(registry_path) if registry_path else Path(__file__).with_name("workflow_registry.json")
        self.registry_snapshot()  # Fail closed on invalid configuration at startup.

    def registry_snapshot(self):
        registry = json.loads(self.registry_path.read_text(encoding="utf-8"))
        _contract.r.validate_active_registry(registry)
        if any(a["workerId"] == "knowledge-mutation" for a in registry["actions"]) or any(k.startswith("knowledge.") for k in registry["requestTypes"]):
            raise ValueError("WORKFLOW_KNOWLEDGE_ROUTE_UNSUPPORTED")
        if "knowledgeGuidance" in registry:
            raise ValueError("WORKFLOW_KNOWLEDGE_GUIDANCE_UNSUPPORTED")
        return registry, {"kind": "workflow-configuration", "version": registry["version"], "sha256": _contract.r.digest(registry)}

    def one_stop_plan(self, payload, *, representative_first=False):
        from .one_stop import select
        character = payload.get('character') if isinstance(payload,dict) else None
        match = self.one_stop_direction_target is None or (
            isinstance(character,dict) and character.get('npcId')==self.one_stop_direction_target)
        streaming = self.one_stop_direction_streaming and match
        if representative_first and (not streaming or self.one_stop_direction_target is None
                or payload.get('projectId') != 'binari' or payload.get('includeCombat') is not False
                or 'combatMotionPresets' in payload):
            raise SelectionError('ONE_STOP_REPRESENTATIVE_PILOT_SCOPE')
        return select(payload, direction_streaming=streaming, representative_first=representative_first)

    def select_workflow(self, payload):
        return _contract.select_workflow(payload, self.registry_snapshot)

    def route_request(self, payload):
        return _contract.route_request(payload, self.registry_snapshot)

    def select(self, intent, inputs=None):
        if self.feature_selector is None:
            raise SelectionError("WORKFLOW_FEATURE_SELECTION_UNAVAILABLE")
        try:
            return self.feature_selector.select(intent, inputs)
        except ValueError as exc:
            raise SelectionError(str(exc)) from exc
