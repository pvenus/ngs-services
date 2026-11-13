from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

from .errors import ExecutionError


class Handler(BaseHTTPRequestHandler):
    service = None

    def _json(self):
        length = int(self.headers.get("Content-Length", "0"))
        try:
            value = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            raise ExecutionError("invalid JSON") from exc
        if not isinstance(value, dict):
            raise ExecutionError("request body must be an object")
        return value

    def _send(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _dispatch(self):
        if self.path == "/health" and self.command == "GET":
            return 200, {"status": "ok", "service": "project-execution-service"}
        parts = [part for part in urlparse(self.path).path.split("/") if part]
        if parts == ["v1", "executions"] and self.command == "POST":
            return 201, self.service.create(self._json())
        if len(parts) == 3 and parts[:2] == ["v1", "executions"] and self.command == "GET":
            return 200, self.service.get(parts[2])
        if len(parts) == 4 and parts[:2] == ["v1", "executions"] and parts[3] == "advance" and self.command == "POST":
            return 200, self.service.advance(parts[2], self._json())
        return 404, {"error": "not_found"}

    def _handle(self):
        try:
            status, value = self._dispatch()
        except ExecutionError as exc:
            status, value = exc.status, {"error": exc.__class__.__name__, "message": str(exc)}
        except Exception as exc:
            status, value = 500, {"error": "internal_error", "message": str(exc)}
        self._send(status, value)

    do_GET = _handle
    do_POST = _handle

    def log_message(self, format, *args):
        return
