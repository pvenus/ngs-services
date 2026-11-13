from __future__ import annotations

import argparse
import hashlib
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .database import DataError, RevisionConflict
from .service import ProjectDataService
from .storage import StorageError
from .domain_contracts.legacy_http import Handler as DomainHandler
from .domains import native


MAX_BODY = 2 * 1024 * 1024


class Handler(DomainHandler):
    server_version = "ProjectDataService/0.1"

    def respond(self, status: int, value: dict | list | None, extra_headers: dict | None = None):
        data = b"" if value is None else json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for key, value_ in (extra_headers or {}).items():
            self.send_header(key, value_)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def read_json(self) -> dict:
        length = self.headers.get("Content-Length", "")
        if not length.isdecimal() or not 0 < int(length) <= MAX_BODY:
            raise DataError("invalid request body size")
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
            raise DataError("application/json is required")
        payload = json.loads(self.rfile.read(int(length)))
        if not isinstance(payload, dict):
            raise DataError("JSON object is required")
        return payload

    def route(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        try:
            if path in ('/api/concept','/api/revisions','/docs/MasterConcept.schema.json','/v1/concept/export') or path.startswith('/api/revisions/'):
                if self.client_address[0]!='127.0.0.1' or self.headers.get('Host') not in ('127.0.0.1:'+str(self.server.server_port),'localhost:'+str(self.server.server_port)) or self.headers.get('Origin') not in (None,'http://127.0.0.1:'+str(self.server.server_port),'http://localhost:'+str(self.server.server_port)):
                    return self.respond(403,{'error':'concept_loopback_required'})
                if parsed.query:return self.respond(422,{'error':'concept_query_unsupported'})
                module=self.server.app.concept
                if path=='/api/concept' and self.command=='PUT':
                    value,replayed=module.save(self.read_json());return self.respond(200,value,{'Idempotency-Replayed':str(replayed).lower()})
                if self.command not in ('GET','HEAD'):return self.respond(405,{'error':'method_not_allowed'})
                if path=='/api/concept':value=module.current()
                elif path=='/api/revisions':value={'revisions':module.history()}
                elif path=='/v1/concept/export':value=module.export()
                elif path=='/docs/MasterConcept.schema.json':value=json.loads((Path(__file__).parent/'concept_validation/schema.json').read_text())
                else:
                    n=path.rsplit('/',1)[-1]
                    if not n.isdecimal():return self.respond(404,{'error':'not_found'})
                    value=module.revision(int(n))
                return self.respond(200,value) if value is not None else self.respond(404,{'error':'concept_not_found'})
            retained=re.fullmatch(r'/v1/knowledge/entries/([^/]+)/revisions/([0-9]+)',path)
            if retained and self.command in ('GET','HEAD'):
                if self.client_address[0]!='127.0.0.1' or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port):return self.respond(403,{'error':'loopback_required'})
                from .domain_export.source import LegacySource
                with self.server.app.domains.lock:
                    value=LegacySource(self.server.app.domains.root).knowledge_entry_revision(unquote(retained.group(1)),int(retained.group(2)))
                return self.respond(200,value) if value else self.respond(404,{'error':'retained_body_unavailable'})
            if path=='/v1/domains/export':
                if self.client_address[0]!='127.0.0.1' or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port) or self.headers.get('Origin') not in (None,'http://127.0.0.1:'+str(self.server.server_port)):return self.respond(403,{'error':'loopback_required'})
                if self.command not in ('GET','HEAD'):return self.respond(405,{'error':'method_not_allowed'})
                if parsed.query not in ('','history=all'):return self.respond(422,{'error':'invalid_export_scope'})
                return self.respond(200,self.server.app.domains.capture(parsed.query=='history=all'))
            if path=='/v1/npc-category/apply' or path.startswith('/v1/npc-category/receipts/'):
                if self.client_address[0]!='127.0.0.1' or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port) or self.headers.get('Origin') not in (None,'http://127.0.0.1:'+str(self.server.server_port)):return self.respond(403,{'error':'loopback_required'})
                if path=='/v1/npc-category/apply' and self.command=='POST':return self.respond(200,self.server.app.domains.category(self.read_json()))
                if self.command=='GET':
                    value=self.server.app.domains.category_receipt(path.rsplit('/',1)[-1]);return self.respond(200,value) if value else self.respond(404,{'error':'receipt_not_found'})
                return self.respond(405,{'error':'method_not_allowed'})
            if path.startswith(('/api/npcs','/api/story','/api/knowledge','/assets/')) or path=='/api/portraits' or path in native.ASSETS:
                return self.server.app.domains.dispatch(self)
            if path in ('/v1/project-display/binari','/v1/project-display/binari/icon') or path.startswith('/v1/project-display/binari/requests/'):
                if self.client_address[0]!='127.0.0.1' or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port) or self.headers.get('Origin') not in (None,'http://127.0.0.1:'+str(self.server.server_port)):
                    return self.respond(403, {'error':'display_loopback_required'})
                if path.endswith('/icon') and self.command=='GET':
                    q=parse_qs(parsed.query,keep_blank_values=True)
                    if set(q)!={'sha256'} or len(q['sha256'])!=1: raise DataError('one icon hash required')
                    value=self.server.app.get_project_display_icon(q['sha256'][0])
                    return self.respond(200,value) if value else self.respond(404,{'error':'display_asset_not_found'})
                if path=='/v1/project-display/binari/requests/history' and self.command=='GET':
                    if parsed.query: raise DataError('display query unsupported')
                    return self.respond(200,self.server.app.get_project_display_history())
                if path.startswith('/v1/project-display/binari/requests/') and self.command=='GET':
                    request_id=path.split('/')[-1]
                    if parsed.query or not re.fullmatch(r'icon\.[a-f0-9]{64}',request_id): raise DataError('invalid display request id')
                    value=self.server.app.get_project_display_receipt(request_id)
                    return self.respond(200,value) if value else self.respond(404,{'error':'display_receipt_not_found'})
                if path!='/v1/project-display/binari': return self.respond(405,{'error':'method_not_allowed'})
                if parsed.query: raise DataError('display query unsupported')
                if self.command in ('GET','HEAD'): return self.respond(200,self.server.app.get_project_display())
                if self.command=='POST':
                    status,value,replayed=self.server.app.put_project_display(self.read_json())
                    return self.respond(status,value,{'Idempotency-Replayed':str(replayed).lower()})
                return self.respond(405, {'error':'method_not_allowed'})
            if path == "/health" and self.command in ("GET", "HEAD"):
                return self.respond(200, {"ok": True, "service": "project-data-service",
                                          "schemaVersion": self.server.app.store.schema_version()})
            if path == "/api/storage/status" and self.command in ("GET", "HEAD"):
                return self.respond(200, self.server.app.storage.status())
            if path == "/api/schema/version" and self.command in ("GET", "HEAD"):
                return self.respond(200, {"version": self.server.app.store.schema_version()})
            if path == "/v1/workflows/sessions" and self.command in ("GET", "HEAD"):
                query = parse_qs(parsed.query, keep_blank_values=True)
                if set(query) - {"status", "limit"}:
                    raise DataError("unsupported workflow list filter")
                status_filter = query.get("status", [None])[0]
                limit_value = query.get("limit", ["100"])[0]
                if not str(limit_value).isdigit():
                    raise DataError("limit must be an integer")
                return self.respond(200, {"items": self.server.app.list_workflow_sessions(
                    status=status_filter, limit=int(limit_value)
                )})
            workflow_events_match = re.fullmatch(r"/v1/workflows/sessions/([^/]+)/events", path)
            if workflow_events_match and self.command in ("GET", "HEAD"):
                session_id = unquote(workflow_events_match.group(1))
                if self.server.app.get_workflow_session(session_id) is None:
                    return self.respond(404, {"error": "session_not_found"})
                return self.respond(200, {"items": self.server.app.workflow_events(session_id)})
            workflow_artifact_match = re.fullmatch(
                r"/v1/workflows/sessions/([^/]+)/artifacts/([^/]+)", path
            )
            if workflow_artifact_match and self.command in ("GET", "HEAD"):
                value = self.server.app.get_workflow_artifact(
                    unquote(workflow_artifact_match.group(1)), unquote(workflow_artifact_match.group(2))
                )
                return self.respond(200, value) if value else self.respond(404, {"error": "artifact_not_found"})
            workflow_steps_match = re.fullmatch(r"/v1/workflows/sessions/([^/]+)/steps", path)
            if workflow_steps_match and self.command in ("GET", "HEAD"):
                value = self.server.app.get_workflow_steps(unquote(workflow_steps_match.group(1)))
                return self.respond(200, value) if value else self.respond(404, {"error": "session_not_found"})
            workflow_session_match = re.fullmatch(r"/v1/workflows/sessions/([^/]+)", path)
            if workflow_session_match and self.command in ("GET", "HEAD"):
                value = self.server.app.get_workflow_session(unquote(workflow_session_match.group(1)))
                return self.respond(200, value) if value else self.respond(404, {"error": "session_not_found"})
            if workflow_session_match and self.command == "PUT":
                status, value, replayed = self.server.app.put_workflow_session(
                    unquote(workflow_session_match.group(1)), self.read_json()
                )
                return self.respond(status, value, {"Idempotency-Replayed": str(replayed).lower()})
            data_item_match = re.fullmatch(r"/v1/data/([^/]+)/([^/]+)", path)
            if data_item_match and self.command in ("GET", "HEAD"):
                value = self.server.app.get_data_resource(
                    unquote(data_item_match.group(1)), unquote(data_item_match.group(2))
                )
                return self.respond(200, value) if value else self.respond(404, {"error": "resource_not_found"})
            if data_item_match and self.command == "PUT":
                status, value, replayed = self.server.app.put_data_resource(
                    unquote(data_item_match.group(1)), unquote(data_item_match.group(2)), self.read_json()
                )
                return self.respond(status, value, {"Idempotency-Replayed": str(replayed).lower()})
            data_list_match = re.fullmatch(r"/v1/data/([^/]+)", path)
            if data_list_match and self.command in ("GET", "HEAD"):
                query = parse_qs(parsed.query, keep_blank_values=True)
                if set(query) - {"status"} or len(query.get("status", [])) > 1:
                    raise DataError("only one status filter is supported")
                status_filter = query.get("status", [None])[0]
                values = self.server.app.list_data_resources(unquote(data_list_match.group(1)), status_filter)
                return self.respond(200, {"items": values})
            if path == "/api/projects" and self.command == "POST":
                status, value, replayed = self.server.app.create_project(self.read_json())
                return self.respond(status, value, {"Idempotency-Replayed": str(replayed).lower()})
            project_match = re.fullmatch(r"/api/projects/([^/]+)", path)
            if project_match and self.command in ("GET", "HEAD"):
                value = self.server.app.get_project(unquote(project_match.group(1)))
                return self.respond(200, value) if value else self.respond(404, {"error": "project_not_found"})
            if project_match and self.command == "PUT":
                status, value, replayed = self.server.app.update_project(
                    unquote(project_match.group(1)), self.read_json()
                )
                return self.respond(status, value, {"Idempotency-Replayed": str(replayed).lower()})
            if path == "/api/assets/register" and self.command == "POST":
                status, value, replayed = self.server.app.register_asset(self.read_json())
                return self.respond(status, value, {"Idempotency-Replayed": str(replayed).lower()})
            asset_match = re.fullmatch(r"/api/assets/([^/]+)", path)
            if asset_match and self.command in ("GET", "HEAD"):
                value = self.server.app.get_asset(unquote(asset_match.group(1)))
                return self.respond(200, value) if value else self.respond(404, {"error": "asset_not_found"})
            audit_match = re.fullmatch(r"/api/audit/([^/]+)/([^/]+)", path)
            if audit_match and self.command in ("GET", "HEAD"):
                value = self.server.app.audit_events(unquote(audit_match.group(1)), unquote(audit_match.group(2)))
                return self.respond(200, {"events": value})
            return self.respond(404, {"error": "not_found"})
        except RevisionConflict as exc:
            return self.respond(409, {"error": str(exc)})
        except (DataError, StorageError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return self.respond(422, {"error": str(exc)})

    def do_GET(self): self.route()
    def do_HEAD(self): self.route()
    def do_POST(self): self.route()
    def do_PUT(self): self.route()


def serve(storage_root: Path, host: str, port: int):
    app = ProjectDataService(storage_root)
    server = ThreadingHTTPServer((host, port), Handler)
    server.app = app
    try:
        print(f"Project Data Service: http://{host}:{server.server_port}", flush=True)
        server.serve_forever()
    finally:
        server.server_close()
        app.close()


def main():
    parser = argparse.ArgumentParser(description="Portable ProjectBS data service")
    parser.add_argument("--storage-root", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8030)
    args = parser.parse_args()
    serve(args.storage_root, args.host, args.port)


if __name__ == "__main__":
    main()
