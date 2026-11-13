from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .data_client import DataServiceError
from .service import DownloadTokenError, FileService, FileServiceError


class Handler(BaseHTTPRequestHandler):
    server_version = "ProjectFileService/0.1"

    def json_response(self, status, value):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data))); self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD": self.wfile.write(data)

    def read_json(self, limit=1024 * 1024):
        value = self.headers.get("Content-Length", "")
        if not value.isdecimal() or not 0 < int(value) <= limit: raise FileServiceError("invalid request size")
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json": raise FileServiceError("JSON required")
        result = json.loads(self.rfile.read(int(value)))
        if not isinstance(result, dict): raise FileServiceError("JSON object required")
        return result

    def route(self):
        path = urlsplit(self.path).path
        try:
            if path == "/health" and self.command in ("GET", "HEAD"):
                return self.json_response(200, {"ok": True, "service": "project-file-service"})
            if path == "/v1/uploads" and self.command == "POST":
                length = self.headers.get("Content-Length", "")
                if not length.isdecimal(): raise FileServiceError("Content-Length required")
                if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/octet-stream":
                    raise FileServiceError("application/octet-stream required")
                result = self.server.app.upload(self.rfile, int(length), project_id=self.headers.get("X-Project-Id", ""),
                    filename=unquote(self.headers.get("X-Filename", "")), kind=self.headers.get("X-Asset-Kind", ""),
                    purpose=self.headers.get("X-Purpose", ""), declared_mime=self.headers.get("X-File-Mime"))
                return self.json_response(201, result)
            asset_get = re.fullmatch(r"/v1/assets/([^/]+)", path)
            if asset_get and self.command in ("GET", "HEAD"):
                result = self.server.app.public_asset(unquote(asset_get.group(1)))
                return self.json_response(200, result) if result is not None else self.json_response(404, {"error": "asset_not_found"})
            link = re.fullmatch(r"/v1/assets/([^/]+)/download-links", path)
            if link and self.command == "POST":
                body = self.read_json()
                result = self.server.app.create_download(unquote(link.group(1)), expires_in=body.get("expiresInSeconds", 86400),
                                                         filename=body.get("requestedFilename"))
                return self.json_response(201, result)
            download = re.fullmatch(r"/v1/downloads/([^/]+)", path)
            if download and self.command in ("GET", "HEAD"):
                asset, file_path, filename = self.server.app.resolve_download(download.group(1))
                size = file_path.stat().st_size; start, end = 0, size - 1; status = 200
                range_header = self.headers.get("Range")
                if range_header:
                    match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
                    if not match or not match.group(1): raise FileServiceError("unsupported Range")
                    start = int(match.group(1)); end = int(match.group(2)) if match.group(2) else end
                    if start > end or end >= size: raise FileServiceError("invalid Range")
                    status = 206
                length = end - start + 1
                self.send_response(status); self.send_header("Content-Type", asset["mimeType"])
                self.send_header("Content-Length", str(length)); self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + __import__('urllib.parse').parse.quote(filename))
                self.send_header("X-Content-Type-Options", "nosniff")
                if status == 206: self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                if self.command != "HEAD":
                    with file_path.open("rb") as source:
                        source.seek(start); remaining = length
                        while remaining:
                            chunk = source.read(min(1024 * 1024, remaining))
                            if not chunk: break
                            self.wfile.write(chunk); remaining -= len(chunk)
                return
            return self.json_response(404, {"error": "not_found"})
        except DownloadTokenError as exc: return self.json_response(403, {"error": str(exc)})
        except (FileServiceError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return self.json_response(422, {"error": str(exc)})
        except DataServiceError as exc: return self.json_response(502, {"error": str(exc)})

    do_GET = route; do_HEAD = route; do_POST = route


def serve(app: FileService, host: str, port: int):
    server = ThreadingHTTPServer((host, port), Handler); server.app = app
    try: server.serve_forever()
    finally: server.server_close()
