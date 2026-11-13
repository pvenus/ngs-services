from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


Status = Literal["draft", "active", "disabled", "legacy"]
Permission = Literal["read_only", "controlled_write", "external_effect", "admin"]
KnowledgeCategory = Literal[
    "common-execution", "storage-file", "service-fixed", "action-execution",
    "codex-template", "validation",
]


def _required(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _string_list(values: list[str], name: str) -> list[str]:
    if not isinstance(values, list) or any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError(f"{name} must contain non-empty strings")
    return list(dict.fromkeys(item.strip() for item in values))


@dataclass(frozen=True)
class ServiceDefinition:
    id: str
    title: str
    purpose: str
    status: Status = "draft"
    fixed_knowledge_ids: list[str] = field(default_factory=list)

    def validate(self) -> "ServiceDefinition":
        _required(self.id, "service.id"); _required(self.title, "service.title"); _required(self.purpose, "service.purpose")
        _string_list(self.fixed_knowledge_ids, "service.fixed_knowledge_ids")
        if self.status not in {"draft", "active", "disabled", "legacy"}: raise ValueError("invalid service status")
        return self

    def to_dict(self) -> dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class ActionDefinition:
    id: str
    service_id: str
    purpose: str
    permission: Permission
    operation: str
    prerequisite_get: str | None = None
    prerequisite_step_output: str | None = None
    required_inputs: list[str] = field(default_factory=list)
    output_type: str = "json"
    knowledge_ids: list[str] = field(default_factory=list)
    template_id: str | None = None
    status: Status = "draft"

    def validate(self) -> "ActionDefinition":
        for value, name in ((self.id, "action.id"), (self.service_id, "action.service_id"),
                            (self.purpose, "action.purpose"), (self.operation, "action.operation"),
                            (self.output_type, "action.output_type")):
            _required(value, name)
        _string_list(self.required_inputs, "action.required_inputs")
        _string_list(self.knowledge_ids, "action.knowledge_ids")
        if self.permission not in {"read_only", "controlled_write", "external_effect", "admin"}:
            raise ValueError("invalid action permission")
        if self.permission != "read_only" and not (self.prerequisite_get or self.prerequisite_step_output):
            raise ValueError("a mutating action requires prerequisite_get or prerequisite_step_output")
        if self.prerequisite_get == self.id:
            raise ValueError("an action cannot use itself as prerequisite_get")
        return self

    def to_dict(self) -> dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class KnowledgeEntry:
    id: str
    category: KnowledgeCategory
    title: str
    purpose: str
    content: dict[str, Any]
    version: int = 1
    status: Status = "draft"

    def validate(self) -> "KnowledgeEntry":
        _required(self.id, "knowledge.id"); _required(self.title, "knowledge.title"); _required(self.purpose, "knowledge.purpose")
        if self.category not in {"common-execution", "storage-file", "service-fixed", "action-execution", "codex-template", "validation"}:
            raise ValueError("invalid knowledge category")
        if not isinstance(self.content, dict): raise ValueError("knowledge.content must be an object")
        if type(self.version) is not int or self.version < 1: raise ValueError("knowledge.version must be positive")
        return self

    def to_dict(self) -> dict[str, Any]: return asdict(self)


@dataclass(frozen=True)
class CodexTemplate:
    id: str
    artifact_type: str
    purpose: str
    required_context_ids: list[str]
    required_inputs: list[str]
    compose_instruction: str
    user_question_conditions: list[dict[str, Any]]
    prerequisite_get: str | None
    execution_tool: str | None
    forbidden_actions: list[str]
    submission_schema: dict[str, Any]
    version: int = 1
    status: Status = "draft"

    def validate(self) -> "CodexTemplate":
        for value, name in ((self.id, "template.id"), (self.artifact_type, "template.artifact_type"),
                            (self.purpose, "template.purpose"), (self.compose_instruction, "template.compose_instruction")):
            _required(value, name)
        _string_list(self.required_context_ids, "template.required_context_ids")
        _string_list(self.required_inputs, "template.required_inputs")
        _string_list(self.forbidden_actions, "template.forbidden_actions")
        if not isinstance(self.user_question_conditions, list): raise ValueError("user_question_conditions must be a list")
        if not isinstance(self.submission_schema, dict) or not self.submission_schema:
            raise ValueError("submission_schema is required")
        if type(self.version) is not int or self.version < 1: raise ValueError("template.version must be positive")
        return self

    def to_dict(self) -> dict[str, Any]: return asdict(self)
