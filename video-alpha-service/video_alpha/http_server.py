import json
import re
import threading
from uuid import uuid4
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

from .service import ConflictError, ValidationError


class Handler(BaseHTTPRequestHandler):
    server_version = "ProjectBSVideoAlpha/1"
    def do_GET(self): self._dispatch()
    def do_POST(self): self._dispatch()
    def _dispatch(self):
        try:
            path = urlparse(self.path).path
            if self.command == "GET" and path == "/health":
                return self._send(200, {"status": "ok", "processorAvailable": self.server.app.processor.available})
            match = re.fullmatch(r"/v1/jobs/([^/]+)", path)
            if self.command == "GET" and match:
                value = self.server.app.get(match.group(1))
                return self._send(200, value) if value else self._send(404, {"error": "not_found"})
            if self.command == "POST" and path == "/v1/jobs":
                return self._send(201, self.server.app.create(self._body(), self.headers.get("X-Request-Id") or "create-" + uuid4().hex))
            run = re.fullmatch(r"/v1/jobs/([^/]+)/run", path)
            if self.command == "POST" and run:
                body = self._body()
                return self._send(200, self.server.app.run(run.group(1), expected_revision=body.get("observedRevision"),
                    confirmed=body.get("confirmed") is True, processing_spec=body.get("processingSpec"),
                    request_id=self.headers.get("X-Request-Id") or "run-" + uuid4().hex))
            run_async = re.fullmatch(r"/v1/jobs/([^/]+)/run-async", path)
            if self.command == "POST" and run_async:
                body = self._body(); job_id = run_async.group(1)
                with self.server.active_lock:
                    current = self.server.app.get(job_id)
                    if not current: raise ValidationError("job not found")
                    if current.get("status") == "completed": return self._send(200, current)
                    if job_id in self.server.active_jobs or current.get("status") == "running":
                        return self._send(202, {"id": job_id, "status": "running", "revision": current["revision"]})
                    self.server.active_jobs.add(job_id)
                request_id = self.headers.get("X-Request-Id") or "run-async-" + uuid4().hex
                def execute():
                    try:
                        self.server.app.run(job_id, expected_revision=body.get("observedRevision"),
                            confirmed=body.get("confirmed") is True, processing_spec=body.get("processingSpec"),
                            request_id=request_id)
                    finally:
                        with self.server.active_lock: self.server.active_jobs.discard(job_id)
                threading.Thread(target=execute, name="video-alpha-" + job_id, daemon=True).start()
                return self._send(202, {"id": job_id, "status": "accepted", "revision": current["revision"]})
            self._send(404, {"error": "not_found"})
        except ValidationError as exc: self._send(422, {"error": "validation", "message": str(exc)})
        except ConflictError as exc: self._send(409, {"error": "conflict", "message": str(exc)})
        except Exception as exc: self._send(502, {"error": "processing_failed", "message": str(exc)})
    def _body(self):
        raw_size = self.headers.get("Content-Length", "")
        if not raw_size.isdecimal() or not 0 < int(raw_size) <= 1024 * 1024:
            raise ValidationError("invalid request body size")
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            raise ValidationError("application/json is required")
        value = json.loads(self.rfile.read(int(raw_size)))
        if not isinstance(value, dict): raise ValidationError("JSON object is required")
        return value
    def _send(self, status, value):
        raw = json.dumps(value, ensure_ascii=False).encode(); self.send_response(status)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def log_message(self, *args): pass
