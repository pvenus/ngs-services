from __future__ import annotations

from typing import Any

from .catalog import CatalogPort


class SelectionError(ValueError):
    pass


class LayaSelectionService:
    """Selects one next action. It never executes tools or reads knowledge bodies."""

    VIDEO_ALPHA_TERMS = ("video", "비디오", "영상", "alpha", "알파", "frame", "프레임", "rgba")
    COMPOSE_TERMS = ("설정", "spec", "스펙", "처리", "분해", "적용", "작성")
    GET_TERMS = ("조회", "상태", "확인", "get", "status")

    def __init__(self, catalog: CatalogPort, scene_source=None):
        self.catalog = catalog
        self.scene_source = scene_source

    @staticmethod
    def _ids(values: list[dict[str, Any]]) -> list[str]:
        return [value["id"] for value in values if isinstance(value.get("id"), str)]

    def _choose_service(self, intent: str, services: list[dict[str, Any]]) -> dict[str, Any]:
        text = intent.casefold()
        candidates = [item for item in services if item.get("id") == "video-alpha" and any(term in text for term in self.VIDEO_ALPHA_TERMS)]
        if len(candidates) != 1:
            raise SelectionError("요청에 맞는 단일 활성 서비스를 선택할 수 없습니다")
        return candidates[0]

    def _choose_action(self, intent: str, service_id: str, actions: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
        text = intent.casefold()
        candidates = [item for item in actions if item.get("service_id") == service_id]
        if any(term in text for term in self.GET_TERMS):
            candidates = [item for item in candidates if item.get("permission") == "read_only"]
            reason = "요청이 현재 작업 상태 조회를 명시했습니다"
        else:
            candidates = [item for item in candidates if item.get("id") == "video-alpha.compose-spec"]
            reason = "비디오 알파 처리 요청에는 실행 전 구조화 설정 작성이 필요합니다"
        if len(candidates) != 1:
            raise SelectionError("요청에 맞는 단일 활성 액션을 선택할 수 없습니다")
        return candidates[0], reason

    def select(self, intent: str, supplied_inputs: dict[str, Any] | None = None) -> dict[str, Any]:
        if not isinstance(intent, str) or not intent.strip():
            raise SelectionError("intent is required")
        if supplied_inputs is None:
            supplied_inputs = {}
        if not isinstance(supplied_inputs, dict):
            raise SelectionError("inputs must be an object")

        feature_id = supplied_inputs.get("featureId")
        portrait_selection=None
        if feature_id=='story.node.image.portrait-reference':
            if self.scene_source is None:raise SelectionError('SCENE_PORTRAIT_CANONICAL_SOURCE_UNAVAILABLE')
            from .scene_portraits import select
            sources=self.scene_source.snapshot()
            portrait_selection=select(sources['story'],sources['npcs'],supplied_inputs.get('target',{}),supplied_inputs.get('presenceReview'))
            if sources!=self.scene_source.snapshot():raise SelectionError('SCENE_PORTRAIT_SOURCE_CHANGED_DURING_SELECTION')
            supplied_inputs=dict(supplied_inputs)
            visible=portrait_selection['selected']
            supplied_inputs['npcId']=visible[0]['npcId'] if visible else None

        if feature_id is None:
            service = self._choose_service(intent, self.catalog.list_services())
            action, reason = self._choose_action(intent, service["id"], self.catalog.list_actions())
        else:
            service, action, reason = self._choose_feature(feature_id)
        context = self.catalog.execution_context(action["id"])
        selected_action = context.get("action")
        if not isinstance(selected_action, dict) or selected_action.get("id") != action["id"]:
            raise SelectionError("catalog returned a mismatched execution context")
        if feature_id is not None and (selected_action.get("permission") != "read_only" or selected_action.get("operation") != "npc_context_prepare"):
            raise SelectionError("NPC context must remain read-only")
        knowledge = context.get("knowledge", [])
        template = context.get("template")
        if not isinstance(knowledge, list) or any(not isinstance(item, dict) for item in knowledge):
            raise SelectionError("catalog returned invalid knowledge references")

        required = list(dict.fromkeys(action.get("required_inputs", []) + ((template or {}).get("required_inputs", []))))
        missing = [name for name in required if supplied_inputs.get(name) in (None, "")]
        prerequisite = action.get("prerequisite_get") or (template or {}).get("prerequisite_get")
        first_tool = prerequisite and self._operation_for(prerequisite)
        prerequisite_inputs = self._prerequisite_inputs(prerequisite)
        missing_prerequisite = [name for name in prerequisite_inputs if supplied_inputs.get(name) in (None, "")]

        if missing_prerequisite:
            next_step = "collect-input"
        elif prerequisite:
            next_step = "call-prerequisite-get"
        elif missing:
            next_step = "collect-input"
        elif template:
            next_step = "compose-codex-artifact"
        else:
            next_step = "ready-for-selected-action"

        result = {
            "serviceId": service["id"],
            "actionId": action["id"],
            "contextEntryIds": self._ids(knowledge),
            "templateId": (template or {}).get("id"),
            "templateSlots": (template or {}).get("required_inputs", []),
            "prerequisiteGet": prerequisite,
            "firstTool": first_tool,
            "missingInputs": missing,
            "nextStep": next_step,
            "selectionReason": reason,
        }

        if portrait_selection is not None:result['portraitSelection']=portrait_selection
        return result

    def _choose_feature(self, feature_id: str):
        # Product-level routing only. No caller-chosen service/action/tool or write execution.
        if feature_id not in ("npc-settings-draft", "portrait.register", "portrait.replace", "portrait.generate", "story.node.image", "story.node.image.portrait-reference", "episode.visual.asset"):
            raise SelectionError("unsupported explicit feature")
        services = [item for item in self.catalog.list_services()
                    if item.get("id") == "npc-planning" and item.get("status") == "active"]
        actions = [item for item in self.catalog.list_actions()
                   if item.get("id") == "npc-planning.prepare-context"
                   and item.get("service_id") == "npc-planning"
                   and item.get("status") == "active" and item.get("permission") == "read_only"
                   and item.get("operation") == "npc_context_prepare"]
        if len(services) != 1 or len(actions) != 1:
            raise SelectionError("NPC read-only preparation is not uniquely registered")
        return services[0], actions[0], "explicit NPC feature selects registered read-only workflow context"

    def _operation_for(self, action_id: str) -> str:
        matches = [item for item in self.catalog.list_actions() if item.get("id") == action_id]
        if len(matches) != 1 or matches[0].get("permission") != "read_only":
            raise SelectionError("prerequisite GET is unavailable or not read-only")
        return matches[0].get("operation")

    def _prerequisite_inputs(self, action_id: str | None) -> list[str]:
        if not action_id:
            return []
        matches = [item for item in self.catalog.list_actions() if item.get("id") == action_id]
        return [] if len(matches) != 1 else list(matches[0].get("required_inputs", []))
