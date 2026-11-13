from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4

from .errors import ConflictError, ValidationError


class WorkflowRepository(Protocol):
    def get_session(self, session_id: str) -> tuple[dict[str, Any], int] | None: ...
    def put_session(self, session_id: str, resource: dict[str, Any], *, expected_revision: int,
                    request_id: str, reason: str) -> tuple[dict[str, Any], int]: ...


class InMemoryWorkflowRepository:
    def __init__(self):
        self.records: dict[str, tuple[dict[str, Any], int]] = {}
        self.requests: dict[str, tuple[str, str, dict[str, Any], int]] = {}

    def get_session(self, session_id: str):
        value = self.records.get(session_id)
        return None if value is None else (deepcopy(value[0]), value[1])

    def put_session(self, session_id, resource, *, expected_revision, request_id, reason):
        fingerprint = json.dumps({"id": session_id, "expected": expected_revision, "resource": resource}, sort_keys=True)
        prior = self.requests.get(request_id)
        if prior:
            if prior[0] != fingerprint:
                raise ValidationError("requestId was reused with a different write")
            return deepcopy(prior[2]), prior[3]
        current = self.records.get(session_id)
        revision = 0 if current is None else current[1]
        if revision != expected_revision:
            raise ConflictError("session revision conflict")
        next_revision = revision + 1
        stored = deepcopy(resource)
        self.records[session_id] = (stored, next_revision)
        self.requests[request_id] = (fingerprint, reason, stored, next_revision)
        return deepcopy(stored), next_revision


class HttpWorkflowRepository:
    """Project Data Service adapter; never exposes SQL or SQLite details."""
    def __init__(self, base_url: str, *, timeout: int = 15,
                 request_id_factory: Callable[[], str] | None = None):
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must use HTTP or HTTPS")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.request_id_factory = request_id_factory or (lambda: str(uuid4()))

    @staticmethod
    def _safe_id(value: str) -> str:
        if not value or "/" in value or value in {".", ".."}:
            raise ValueError("invalid session id")
        return quote(value, safe="")

    def _request(self, method: str, path: str, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(self.base_url + path, data=data, method=method,
                          headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raw = exc.read()
            return exc.code, json.loads(raw.decode("utf-8")) if raw else {}
        except URLError as exc:
            raise RuntimeError("Project Data Service unavailable") from exc

    @staticmethod
    def _unwrap(status, body):
        if status == 404:
            return None
        if status == 409:
            raise ConflictError("session revision conflict")
        if status == 422:
            raise ValidationError(str(body.get("error", "invalid workflow resource")))
        if not 200 <= status < 300:
            raise RuntimeError(f"Project Data Service returned {status}")
        if not isinstance(body.get("resource"), dict) or type(body.get("revision")) is not int:
            raise RuntimeError("invalid workflow record envelope")
        return body["resource"], body["revision"]

    def get_session(self, session_id):
        status, body = self._request("GET", f"/v1/workflows/sessions/{self._safe_id(session_id)}")
        return self._unwrap(status, body)

    def put_session(self, session_id, resource, *, expected_revision, request_id, reason):
        status, body = self._request("PUT", f"/v1/workflows/sessions/{self._safe_id(session_id)}", {
            "requestId": request_id,
            "expectedRevision": expected_revision,
            "actor": "project-execution-service",
            "reason": reason,
            "resource": resource,
        })
        result = self._unwrap(status, body)
        if result is None:
            raise RuntimeError("PUT returned not found")
        return result
