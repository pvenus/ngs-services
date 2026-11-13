from __future__ import annotations

import json
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class UpstreamError(RuntimeError):
    def __init__(self, service: str, status: int, message: str):
        super().__init__(f"{service}: {message}")
        self.service, self.status = service, status


class ServiceClient:
    def __init__(self, service: str, base_url: str, transport: Callable | None = None, timeout: int = 1800):
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must use HTTP or HTTPS")
        self.service, self.base_url = service, base_url.rstrip("/")
        self.transport, self.timeout = transport, timeout

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None,
                *, allow_not_found: bool = False) -> dict[str, Any] | None:
        if self.transport:
            status, body = self.transport(method, path, payload)
        else:
            data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
            request = Request(self.base_url + path, data=data, method=method,
                              headers={"Content-Type": "application/json", "Accept": "application/json"})
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    status = response.status
                    body = json.loads(response.read().decode() or "{}")
            except HTTPError as exc:
                status = exc.code
                try: body = json.loads(exc.read().decode() or "{}")
                except Exception: body = {}
            except (URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise UpstreamError(self.service, 503, "upstream unavailable") from exc
        if status == 404 and allow_not_found:
            return None
        if not 200 <= status < 300 or not isinstance(body, dict):
            message = body.get("message") or body.get("error") if isinstance(body, dict) else "invalid response"
            raise UpstreamError(self.service, status, str(message or "upstream request failed"))
        return body

    @staticmethod
    def ident(value: Any) -> str:
        if not isinstance(value, str) or not value or "/" in value or value in {".", ".."}:
            raise ValueError("invalid id")
        return quote(value, safe="")
