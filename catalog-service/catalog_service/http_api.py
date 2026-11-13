from __future__ import annotations

import json
import re
from dataclasses import fields
from typing import Any, Callable

from .models import ActionDefinition, CodexTemplate, KnowledgeEntry, ServiceDefinition
from .service import CatalogNotFoundError, CatalogService, CatalogValidationError


MODEL_BY_PATH = {
    "/v1/catalog/services": ("services", ServiceDefinition),
    "/v1/catalog/actions": ("actions", ActionDefinition),
    "/v1/catalog/knowledge": ("knowledge", KnowledgeEntry),
    "/v1/catalog/templates": ("templates", CodexTemplate),
}

SAFE_RESOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _construct(model, payload: dict[str, Any]):
    allowed = {item.name for item in fields(model)}
    unknown = set(payload) - allowed
    if unknown: raise CatalogValidationError(f"unknown fields: {', '.join(sorted(unknown))}")
    try: return model(**payload)
    except (TypeError, ValueError) as exc: raise CatalogValidationError(str(exc)) from exc


class CatalogApi:
    """Small WSGI domain API. Production injects an HTTP-backed Data Service repository."""
    def __init__(self, service: CatalogService): self.service = service

    @staticmethod
    def _response(start_response: Callable, status: str, payload: Any):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        start_response(status, [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(data)))])
        return [data]

    def __call__(self, environ, start_response):
        path, method = environ.get("PATH_INFO", ""), environ.get("REQUEST_METHOD", "GET")
        try:
            if path == "/health" and method == "GET":
                return self._response(start_response, "200 OK", {"ok": True, "service": "project-catalog-service"})
            if path == "/v1/catalog/knowledge/query" and method == "POST":
                length = int(environ.get("CONTENT_LENGTH") or "0")
                if not 0 < length <= 64_000:
                    raise CatalogValidationError("invalid body size")
                payload = json.loads(environ["wsgi.input"].read(length).decode("utf-8"))
                if not isinstance(payload, dict) or set(payload) != {"ids"}:
                    raise CatalogValidationError("body must contain only ids")
                ids = payload["ids"]
                if not isinstance(ids, list) or not 1 <= len(ids) <= 20:
                    raise CatalogValidationError("ids must contain between 1 and 20 entries")
                if any(not isinstance(entry_id, str) or not SAFE_RESOURCE_ID.fullmatch(entry_id) for entry_id in ids):
                    raise CatalogValidationError("invalid knowledge id")
                entries, missing_ids = [], []
                for entry_id in ids:
                    knowledge = self.service.repository.get("knowledge", entry_id)
                    if knowledge is None:
                        missing_ids.append(entry_id)
                    else:
                        entries.append(knowledge)
                return self._response(start_response, "200 OK", {"entries": entries, "missingIds": missing_ids})
            if path.startswith("/v1/catalog/knowledge/") and method == "GET":
                knowledge_id = path[len("/v1/catalog/knowledge/"):]
                if not SAFE_RESOURCE_ID.fullmatch(knowledge_id):
                    raise CatalogValidationError("invalid knowledge id")
                knowledge = self.service.repository.get("knowledge", knowledge_id)
                if knowledge is None:
                    raise CatalogNotFoundError("knowledge not found")
                return self._response(start_response, "200 OK", knowledge)
            if path.startswith("/v1/catalog/execution-context/") and method == "GET":
                action_id = path.removeprefix("/v1/catalog/execution-context/")
                return self._response(start_response, "200 OK", self.service.get_execution_context(action_id))
            if path in MODEL_BY_PATH:
                collection, model = MODEL_BY_PATH[path]
                if method == "GET":
                    status = environ.get("QUERY_STRING", "")
                    status = status.removeprefix("status=") if status.startswith("status=") else None
                    return self._response(start_response, "200 OK", {collection: self.service.repository.list(collection, status=status)})
                if method == "PUT":
                    length = int(environ.get("CONTENT_LENGTH") or "0")
                    if not 0 < length <= 1_000_000: raise CatalogValidationError("invalid body size")
                    payload = json.loads(environ["wsgi.input"].read(length).decode("utf-8"))
                    value = _construct(model, payload)
                    method_name = {"services": "put_service", "actions": "put_action", "knowledge": "put_knowledge", "templates": "put_template"}[collection]
                    saved = getattr(self.service, method_name)(value)
                    return self._response(start_response, "200 OK", saved)
            return self._response(start_response, "404 Not Found", {"error": "not_found"})
        except CatalogNotFoundError as exc:
            return self._response(start_response, "404 Not Found", {"error": str(exc)})
        except (CatalogValidationError, ValueError, json.JSONDecodeError) as exc:
            return self._response(start_response, "422 Unprocessable Entity", {"error": str(exc)})
