from __future__ import annotations

import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .client import UpstreamError
from .service import WorkflowError


class Handler(BaseHTTPRequestHandler):
    server_version = "ProjectBSWorkflow/0.1"

    def _body(self):
        length = self.headers.get("Content-Length", "")
        if not length.isdecimal() or not 0 < int(length) <= 1024 * 1024:
            raise WorkflowError("invalid request size")
        value = json.loads(self.rfile.read(int(length)))
        if not isinstance(value, dict): raise WorkflowError("JSON object required")
        return value

    def _send(self, status, value):
        raw = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

    def _dispatch(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if self.command == "GET" and path == "/health":
            return 200, {"ok": True, "service": "projectbs-workflow-service"}
        if self.command == "GET" and path == "/v1/tracking/workflows":
            query = parse_qs(parsed.query)
            status = query.get("status", [None])[0]
            limit_text = query.get("limit", ["100"])[0]
            if not limit_text.isdigit(): raise WorkflowError("limit must be an integer")
            return 200, self.server.app.list_runs(status, int(limit_text))
        trace_match = re.fullmatch(r"/v1/tracking/workflows/([^/]+)/trace", path)
        if self.command == "GET" and trace_match:
            return 200, self.server.app.trace(unquote(trace_match.group(1)))
        if self.command == "POST" and path == "/v1/workflows":
            return 201, self.server.app.start(self._body())
        match = re.fullmatch(r"/v1/workflows/([^/]+)(?:/(submit|execute|result))?", path)
        if not match: return 404, {"error": "not_found"}
        session_id, action = unquote(match.group(1)), match.group(2)
        if self.command == "GET" and action is None: return 200, self.server.app.get(session_id)
        if self.command == "POST" and action == "submit": return 200, self.server.app.submit(session_id, self._body())
        if self.command == "POST" and action == "execute": return 200, self.server.app.execute_next(session_id, self._body())
        if self.command == "GET" and action == "result": return 200, self.server.app.result(session_id)
        return 405, {"error": "method_not_allowed"}

    def _static(self):
        path = urlsplit(self.path).path
        name = "index.html" if path in {"/", "/workflows", "/workflows/"} else path.removeprefix("/static/")
        if path not in {"/", "/workflows", "/workflows/"} and not path.startswith("/static/"):
            return False
        if name not in {"index.html", "workflow.css", "workflow.js"}:
            self._send(404, {"error": "not_found"}); return True
        file_path = Path(__file__).with_name("static") / name
        if not file_path.is_file():
            self._send(404, {"error": "not_found"}); return True
        raw = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(raw)
        return True

    def route(self):
        if self.command == "GET" and self._static(): return
        try: status, value = self._dispatch()
        except (WorkflowError, ValueError, json.JSONDecodeError) as exc: status, value = 422, {"error": str(exc)}
        except UpstreamError as exc: status, value = 502, {"error": "upstream_error", "service": exc.service, "message": str(exc)}
        except Exception as exc: status, value = 500, {"error": "internal_error", "message": str(exc)}
        self._send(status, value)

    do_GET = route
    do_POST = route
    def log_message(self, *args): pass


def serve(app, host, port):
    server = ThreadingHTTPServer((host, port), Handler); server.app = app
    try: server.serve_forever()
    finally: server.server_close()
