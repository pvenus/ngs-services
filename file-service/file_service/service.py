from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import time
import unicodedata
from pathlib import Path
from typing import Any
from uuid import uuid4

from .data_client import DataClient


class FileServiceError(ValueError): pass
class DownloadTokenError(FileServiceError): pass


SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class FileService:
    def __init__(self, storage_root: Path, data_client: DataClient, secret: bytes,
                 *, max_upload_bytes: int = 512 * 1024 * 1024, max_download_bytes: int = 4 * 1024 * 1024 * 1024):
        self.root = storage_root.resolve()
        self.data_client = data_client
        self.secret = secret
        self.max_upload_bytes = max_upload_bytes
        self.max_download_bytes = max_download_bytes
        if len(secret) < 32: raise ValueError("download token secret must be at least 32 bytes")
        for name in ("temporary", "assets/uploads", "assets/generated"):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def safe_id(value: str, label: str) -> str:
        if not isinstance(value, str) or not SAFE_ID.fullmatch(value): raise FileServiceError(f"invalid {label}")
        return value

    @staticmethod
    def safe_filename(value: str) -> str:
        if not isinstance(value, str): raise FileServiceError("invalid filename")
        value = unicodedata.normalize("NFKC", value).strip()
        if not value or len(value.encode("utf-8")) > 255 or value in {".", ".."}:
            raise FileServiceError("invalid filename")
        if any(char in value for char in ("/", "\\", "\x00", "\r", "\n")):
            raise FileServiceError("invalid filename")
        return value

    def resolve(self, relative_path: str, *, must_exist: bool = False) -> Path:
        if not isinstance(relative_path, str) or not relative_path or Path(relative_path).is_absolute():
            raise FileServiceError("managed relative path is required")
        parts = Path(relative_path).parts
        if ".." in parts or "." in parts or "\\" in relative_path:
            raise FileServiceError("unsafe managed path")
        path = self.root.joinpath(*parts)
        candidate = path.resolve(strict=must_exist)
        if candidate != self.root and self.root not in candidate.parents:
            raise FileServiceError("managed path escapes storage root")
        return path

    @staticmethod
    def sniff_mime(filename: str, declared: str | None, prefix: bytes) -> str:
        signatures = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"),
                      (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif"))
        detected = next((mime for magic, mime in signatures if prefix.startswith(magic)), None)
        if prefix[4:8] == b"ftyp": detected = "video/mp4"
        guessed = mimetypes.guess_type(filename)[0]
        mime = detected or guessed or "application/octet-stream"
        if declared and declared != "application/octet-stream" and detected and declared != detected:
            raise FileServiceError("declared MIME does not match file signature")
        return mime

    def upload(self, stream, length: int, *, project_id: str, filename: str, kind: str,
               purpose: str, declared_mime: str | None = None, actor: str = "file-service") -> dict[str, Any]:
        project_id = self.safe_id(project_id, "project id")
        kind = self.safe_id(kind, "asset kind")
        filename = self.safe_filename(filename)
        if not isinstance(purpose, str) or not purpose.strip(): raise FileServiceError("purpose is required")
        if length <= 0 or length > self.max_upload_bytes: raise FileServiceError("invalid upload size")
        temp = self.root / "temporary" / (uuid4().hex + ".upload")
        digest = hashlib.sha256(); remaining = length; prefix = b""
        try:
            with temp.open("xb") as output:
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk: raise FileServiceError("incomplete upload")
                    if not prefix: prefix = chunk[:32]
                    digest.update(chunk); output.write(chunk); remaining -= len(chunk)
                output.flush(); os.fsync(output.fileno())
            mime = self.sniff_mime(filename, declared_mime, prefix)
            asset_id = "asset." + uuid4().hex
            suffix = Path(filename).suffix.lower()[:16]
            relative = f"assets/uploads/{asset_id}/original{suffix}"
            destination = self.resolve(relative)
            destination.parent.mkdir(parents=True, exist_ok=False)
            os.replace(temp, destination)
            payload = {
                "requestId": "request." + uuid4().hex, "actor": actor, "reason": purpose.strip(),
                "asset": {"id": asset_id, "projectId": project_id, "kind": kind,
                          "relativePath": relative, "mimeType": mime, "originalName": filename,
                          "origin": "external-upload"},
            }
            try: return self.data_client.register_asset(payload)
            except Exception:
                destination.unlink(missing_ok=True)
                destination.parent.rmdir()
                raise
        finally:
            temp.unlink(missing_ok=True)

    def register_internal(self, *, asset_id: str, project_id: str, relative_path: str, filename: str,
                          kind: str, mime_type: str, reason: str, actor: str) -> dict[str, Any]:
        self.safe_id(asset_id, "asset id"); self.safe_id(project_id, "project id"); self.safe_id(kind, "asset kind")
        filename = self.safe_filename(filename)
        if not relative_path.startswith("assets/generated/"): raise FileServiceError("internal file must be under assets/generated")
        path = self.resolve(relative_path, must_exist=True)
        if not path.is_file() or path.is_symlink(): raise FileServiceError("internal asset must be a regular file")
        return self.data_client.register_asset({
            "requestId": "request." + uuid4().hex, "actor": actor, "reason": reason,
            "asset": {"id": asset_id, "projectId": project_id, "kind": kind, "relativePath": relative_path,
                      "mimeType": mime_type, "originalName": filename, "origin": "internal-generation"},
        })

    def create_download(self, asset_id: str, *, expires_in: int, filename: str | None = None) -> dict[str, Any]:
        asset = self.data_client.get_asset(self.safe_id(asset_id, "asset id"))
        if not asset or asset.get("status") != "available": raise FileServiceError("asset is unavailable")
        if not 1 <= expires_in <= 7 * 24 * 3600: raise FileServiceError("expiry must be between 1 second and 7 days")
        output_name = self.safe_filename(filename or asset["originalName"])
        payload = {"a": asset_id, "e": int(time.time()) + expires_in, "f": output_name, "n": secrets.token_hex(8)}
        encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=")
        signature = hmac.new(self.secret, encoded, hashlib.sha256).digest()
        token = encoded.decode() + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
        return {"assetId": asset_id, "token": token, "expiresAt": payload["e"], "filename": output_name,
                "size": asset["size"], "downloadPath": "/v1/downloads/" + token}

    def public_asset(self, asset_id: str) -> dict[str, Any] | None:
        """Return an allowlisted projection; never expose a managed storage location."""
        asset = self.data_client.get_asset(self.safe_id(asset_id, "asset id"))
        if asset is None:
            return None
        allowed = ("id", "projectId", "kind", "origin", "originalName", "mimeType",
                   "size", "sha256", "status", "revision", "createdAt", "updatedAt")
        return {key: asset[key] for key in allowed if key in asset}

    def resolve_download(self, token: str) -> tuple[dict[str, Any], Path, str]:
        try:
            encoded_text, signature_text = token.split(".", 1)
            encoded = encoded_text.encode()
            signature = base64.urlsafe_b64decode(signature_text + "=" * (-len(signature_text) % 4))
            canonical_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
            if not hmac.compare_digest(signature_text, canonical_signature):
                raise DownloadTokenError("invalid download token")
            if not hmac.compare_digest(signature, hmac.new(self.secret, encoded, hashlib.sha256).digest()):
                raise DownloadTokenError("invalid download token")
            payload = json.loads(base64.urlsafe_b64decode(encoded_text + "=" * (-len(encoded_text) % 4)))
        except DownloadTokenError: raise
        except Exception as exc: raise DownloadTokenError("invalid download token") from exc
        if type(payload.get("e")) is not int or payload["e"] < int(time.time()): raise DownloadTokenError("download token expired")
        asset = self.data_client.get_asset(payload.get("a", ""))
        if not asset or asset.get("status") != "available": raise DownloadTokenError("asset is unavailable")
        path = self.resolve(asset["relativePath"], must_exist=True)
        if not path.is_file() or path.is_symlink() or path.stat().st_size > self.max_download_bytes:
            raise DownloadTokenError("asset file is unavailable")
        return asset, path, self.safe_filename(payload["f"])
