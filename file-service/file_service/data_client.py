from __future__ import annotations

import json
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class DataServiceError(RuntimeError): pass


class DataClient(Protocol):
    def register_asset(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def get_asset(self, asset_id: str) -> dict[str, Any] | None: ...


class HttpDataClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("Data Service URL must use HTTP or HTTPS")

    def _request(self, request: Request) -> dict[str, Any] | None:
        try:
            with urlopen(request, timeout=15) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except HTTPError as exc:
            if exc.code == 404: return None
            raise DataServiceError(f"Data Service returned {exc.code}") from exc
        except URLError as exc:
            raise DataServiceError("Data Service is unavailable") from exc

    def register_asset(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        result = self._request(Request(
            self.base_url + "/api/assets/register", data=body, method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        ))
        if not isinstance(result, dict): raise DataServiceError("invalid register response")
        return result

    def get_asset(self, asset_id: str) -> dict[str, Any] | None:
        return self._request(Request(
            self.base_url + "/api/assets/" + quote(asset_id, safe=""),
            method="GET", headers={"Accept": "application/json"},
        ))

