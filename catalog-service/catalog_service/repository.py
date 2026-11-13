from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from uuid import uuid4


class RepositoryError(RuntimeError): pass
class RepositoryNotFound(RepositoryError): pass
class RepositoryConflict(RepositoryError): pass
class RepositoryValidationError(RepositoryError): pass
class RepositoryTransportError(RepositoryError): pass


class HttpTransport(Protocol):
    def request(self, method: str, url: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]: ...


class UrllibTransport:
    def request(self, method: str, url: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(url, data=body, method=method, headers={"Accept": "application/json", "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=15) as response:
                raw = response.read()
                return response.status, json.loads(raw.decode("utf-8")) if raw else {}
        except HTTPError as exc:
            raw = exc.read()
            try: value = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError): value = {"error": "invalid_error_response"}
            return exc.code, value
        except URLError as exc:
            raise RepositoryTransportError("Project Data Service is unavailable") from exc


class CatalogRepository(Protocol):
    """Persistence port implemented by the shared Project Data Service adapter."""
    def get(self, collection: str, resource_id: str) -> dict[str, Any] | None: ...
    def list(self, collection: str, *, status: str | None = None) -> list[dict[str, Any]]: ...
    def put(self, collection: str, resource_id: str, value: dict[str, Any]) -> dict[str, Any]: ...


class HttpCatalogRepository:
    """Production adapter for the shared Project Data Service's constrained resource API."""
    COLLECTIONS = frozenset({"services", "actions", "knowledge", "templates"})
    FIELD_MAPS = {
        "services": {"fixed_knowledge_ids": "fixedKnowledgeIds"},
        "actions": {
            "service_id": "serviceId", "prerequisite_get": "prerequisiteGet",
            "prerequisite_step_output": "prerequisiteStepOutput", "required_inputs": "requiredInputs",
            "output_type": "outputType", "knowledge_ids": "knowledgeIds", "template_id": "templateId",
        },
        "knowledge": {"entry_type": "entryType"},
        "templates": {
            "artifact_type": "artifactType", "required_context_ids": "requiredContextIds",
            "required_inputs": "requiredInputs", "compose_instruction": "composeInstruction",
            "user_question_conditions": "userQuestionConditions", "prerequisite_get": "prerequisiteGet",
            "execution_tool": "executionTool", "forbidden_actions": "forbiddenActions",
            "submission_schema": "submissionSchema",
        },
    }

    def __init__(
        self, base_url: str, *, transport: HttpTransport | None = None,
        request_id_factory: Callable[[], str] | None = None,
        write_reason: str = "project catalog update",
    ):
        self.base_url = base_url.rstrip("/")
        if not self.base_url.startswith(("http://", "https://")): raise ValueError("base_url must use HTTP or HTTPS")
        self.transport = transport or UrllibTransport()
        self.request_id_factory = request_id_factory or (lambda: str(uuid4()))
        self.write_reason = write_reason

    def _path(self, collection: str, resource_id: str | None = None, query: dict[str, str] | None = None) -> str:
        if collection not in self.COLLECTIONS: raise ValueError(f"unsupported collection: {collection}")
        path = f"{self.base_url}/v1/data/{quote(collection, safe='')}"
        if resource_id is not None:
            if not resource_id or "/" in resource_id or resource_id in {".", ".."}: raise ValueError("invalid resource id")
            path += f"/{quote(resource_id, safe='')}"
        if query: path += "?" + urlencode(query)
        return path

    def _request(self, method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        status, body = self.transport.request(method, url, payload)
        detail = str(body.get("error", body.get("message", "request failed")))
        if status == 404: raise RepositoryNotFound(detail)
        if status == 409: raise RepositoryConflict(detail)
        if status == 422: raise RepositoryValidationError(detail)
        if not 200 <= status < 300: raise RepositoryTransportError(f"Project Data Service returned {status}: {detail}")
        if not isinstance(body, dict): raise RepositoryTransportError("Project Data Service returned a non-object response")
        return body

    def _serialize_resource(self, collection: str, value: dict[str, Any]) -> dict[str, Any]:
        mapping = self.FIELD_MAPS[collection]
        serialized = {mapping.get(key, key): deepcopy(item) for key, item in value.items()}
        if collection == "knowledge":
            serialized.setdefault("entryType", "structured-guidance")
        return serialized

    def _deserialize_resource(self, collection: str, value: dict[str, Any]) -> dict[str, Any]:
        reverse = {stored: domain for domain, stored in self.FIELD_MAPS[collection].items()}
        deserialized = {reverse.get(key, key): deepcopy(item) for key, item in value.items()}
        if collection == "knowledge":
            deserialized.pop("entry_type", None)
        return deserialized

    def _record_envelope(self, collection: str, body: dict[str, Any]) -> dict[str, Any]:
        resource = body.get("resource")
        revision = body.get("revision")
        if not isinstance(resource, dict):
            raise RepositoryTransportError("missing resource in record envelope")
        if type(revision) is not int or revision < 1:
            raise RepositoryTransportError("record envelope has an invalid revision")
        return {**body, "resource": self._deserialize_resource(collection, resource)}

    def _get_envelope(self, collection: str, resource_id: str) -> dict[str, Any] | None:
        try:
            body = self._request("GET", self._path(collection, resource_id))
        except RepositoryNotFound:
            return None
        return self._record_envelope(collection, body)

    def get(self, collection: str, resource_id: str) -> dict[str, Any] | None:
        envelope = self._get_envelope(collection, resource_id)
        return None if envelope is None else envelope["resource"]

    def list(self, collection: str, *, status: str | None = None) -> list[dict[str, Any]]:
        query = {"status": status} if status is not None else None
        body = self._request("GET", self._path(collection, query=query))
        items = body.get("items")
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise RepositoryTransportError("missing items in list response")
        return [self._record_envelope(collection, item)["resource"] for item in items]

    def put(self, collection: str, resource_id: str, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("id") != resource_id: raise ValueError("resource id mismatch")
        current = self._get_envelope(collection, resource_id)
        expected_revision = 0 if current is None else current["revision"]
        body = self._request("PUT", self._path(collection, resource_id), {
            "requestId": self.request_id_factory(), "expectedRevision": expected_revision,
            "reason": self.write_reason, "resource": self._serialize_resource(collection, value),
        })
        return self._record_envelope(collection, body)["resource"]


class InMemoryCatalogRepository:
    """Test-only repository. It never creates a file database."""
    COLLECTIONS = {"services", "actions", "knowledge", "templates"}

    def __init__(self):
        self._data: dict[str, dict[str, dict[str, Any]]] = {name: {} for name in self.COLLECTIONS}

    def _collection(self, name: str) -> dict[str, dict[str, Any]]:
        if name not in self._data: raise ValueError(f"unsupported collection: {name}")
        return self._data[name]

    def get(self, collection: str, resource_id: str) -> dict[str, Any] | None:
        value = self._collection(collection).get(resource_id)
        return deepcopy(value) if value is not None else None

    def list(self, collection: str, *, status: str | None = None) -> list[dict[str, Any]]:
        values = self._collection(collection).values()
        return [deepcopy(item) for item in values if status is None or item.get("status") == status]

    def put(self, collection: str, resource_id: str, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("id") != resource_id: raise ValueError("resource id mismatch")
        self._collection(collection)[resource_id] = deepcopy(value)
        return deepcopy(value)
