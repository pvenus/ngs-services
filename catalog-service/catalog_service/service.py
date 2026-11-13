from __future__ import annotations

from typing import Any

from .models import ActionDefinition, CodexTemplate, KnowledgeEntry, ServiceDefinition
from .repository import CatalogRepository


class CatalogValidationError(ValueError): pass
class CatalogNotFoundError(LookupError): pass


class CatalogService:
    def __init__(self, repository: CatalogRepository): self.repository = repository

    @staticmethod
    def _validated(model):
        try: return model.validate()
        except ValueError as exc: raise CatalogValidationError(str(exc)) from exc

    def put_service(self, value: ServiceDefinition) -> dict[str, Any]:
        value = self._validated(value)
        missing = [item for item in value.fixed_knowledge_ids if self.repository.get("knowledge", item) is None]
        if missing: raise CatalogValidationError(f"unknown service knowledge ids: {', '.join(missing)}")
        return self.repository.put("services", value.id, value.to_dict())

    def put_action(self, value: ActionDefinition) -> dict[str, Any]:
        value = self._validated(value)
        if self.repository.get("services", value.service_id) is None:
            raise CatalogValidationError("action.service_id does not exist")
        if value.prerequisite_get:
            prerequisite = self.repository.get("actions", value.prerequisite_get)
            if prerequisite is None or prerequisite.get("permission") != "read_only":
                raise CatalogValidationError("prerequisite_get must reference a read-only action")
        if value.template_id and self.repository.get("templates", value.template_id) is None:
            raise CatalogValidationError("action.template_id does not exist")
        missing = [item for item in value.knowledge_ids if self.repository.get("knowledge", item) is None]
        if missing: raise CatalogValidationError(f"unknown action knowledge ids: {', '.join(missing)}")
        return self.repository.put("actions", value.id, value.to_dict())

    def put_knowledge(self, value: KnowledgeEntry) -> dict[str, Any]:
        value = self._validated(value)
        return self.repository.put("knowledge", value.id, value.to_dict())

    def put_template(self, value: CodexTemplate) -> dict[str, Any]:
        value = self._validated(value)
        missing = [item for item in value.required_context_ids if self.repository.get("knowledge", item) is None]
        if missing: raise CatalogValidationError(f"unknown template context ids: {', '.join(missing)}")
        return self.repository.put("templates", value.id, value.to_dict())

    def list_services(self, status: str | None = "active") -> list[dict[str, Any]]:
        return self.repository.list("services", status=status)

    def get_execution_context(self, action_id: str) -> dict[str, Any]:
        action = self.repository.get("actions", action_id)
        if action is None: raise CatalogNotFoundError("action not found")
        service = self.repository.get("services", action["service_id"])
        if service is None: raise CatalogNotFoundError("service not found")
        ids = list(dict.fromkeys(service.get("fixed_knowledge_ids", []) + action.get("knowledge_ids", [])))
        knowledge = []
        for entry_id in ids:
            entry = self.repository.get("knowledge", entry_id)
            if entry is None: raise CatalogValidationError(f"missing execution knowledge: {entry_id}")
            knowledge.append(entry)
        template = self.repository.get("templates", action["template_id"]) if action.get("template_id") else None
        return {"service": service, "action": action, "knowledge": knowledge, "template": template}
