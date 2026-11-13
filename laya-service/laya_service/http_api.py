from __future__ import annotations

import json

from .catalog import CatalogUnavailable
from .service import LayaSelectionService, SelectionError


class LayaApi:
    def __init__(self, service: LayaSelectionService):
        self.service = service

    @staticmethod
    def _response(start_response, status: str, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        start_response(status, [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(data)))])
        return [data]

    def __call__(self, environ, start_response):
        path, method = environ.get("PATH_INFO", ""), environ.get("REQUEST_METHOD", "GET")
        try:
            if path == "/health" and method == "GET":
                return self._response(start_response, "200 OK", {"ok": True, "service": "project-laya-service"})
            if path == "/v1/selections" and method == "POST":
                length = int(environ.get("CONTENT_LENGTH") or "0")
                if not 0 < length <= 100_000:
                    raise SelectionError("invalid body size")
                payload = json.loads(environ["wsgi.input"].read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise SelectionError("body must be an object")
                return self._response(start_response, "200 OK", self.service.select(payload.get("intent"), payload.get("inputs")))
            return self._response(start_response, "404 Not Found", {"error": "not_found"})
        except (SelectionError, ValueError, json.JSONDecodeError) as exc:
            return self._response(start_response, "422 Unprocessable Entity", {"error": str(exc)})
        except CatalogUnavailable as exc:
            return self._response(start_response, "503 Service Unavailable", {"error": str(exc)})
