from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class CatalogUnavailable(RuntimeError):
    pass


class CatalogPort(Protocol):
    def list_services(self) -> list[dict[str, Any]]: ...
    def list_actions(self) -> list[dict[str, Any]]: ...
    def execution_context(self, action_id: str) -> dict[str, Any]: ...


class CatalogClient:
    """Read-only adapter for Project Catalog Service's public HTTP API."""

    def __init__(self, base_url: str, *, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("catalog base_url must use HTTP or HTTPS")
        self.timeout = timeout

    def _get(self, path: str, query: dict[str, str] | None = None) -> dict[str, Any]:
        url = self.base_url + path
        if query:
            url += "?" + urlencode(query)
        request = Request(url, method="GET", headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CatalogUnavailable("Project Catalog Service is unavailable") from exc
        if not isinstance(body, dict):
            raise CatalogUnavailable("Project Catalog Service returned a non-object")
        return body

    @staticmethod
    def _items(body: dict[str, Any], key: str) -> list[dict[str, Any]]:
        value = body.get(key)
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise CatalogUnavailable(f"catalog response is missing {key}")
        return value

    def list_services(self) -> list[dict[str, Any]]:
        return self._items(self._get("/v1/catalog/services", {"status": "active"}), "services")

    def list_actions(self) -> list[dict[str, Any]]:
        return self._items(self._get("/v1/catalog/actions", {"status": "active"}), "actions")

    def execution_context(self, action_id: str) -> dict[str, Any]:
        if not action_id or "/" in action_id or action_id in {".", ".."}:
            raise ValueError("invalid action id")
        # Catalog builds this context from versioned execution configuration;
        # it does not access the removed user knowledge service.
        return self._get("/v1/catalog/execution-context/" + quote(action_id, safe=""))


class InMemoryCatalogClient:
    def __init__(self, services: list[dict[str, Any]], actions: list[dict[str, Any]], contexts: dict[str, dict[str, Any]]):
        self.services, self.actions, self.contexts = deepcopy(services), deepcopy(actions), deepcopy(contexts)

    def list_services(self) -> list[dict[str, Any]]:
        return deepcopy([item for item in self.services if item.get("status") == "active"])

    def list_actions(self) -> list[dict[str, Any]]:
        return deepcopy([item for item in self.actions if item.get("status") == "active"])

    def execution_context(self, action_id: str) -> dict[str, Any]:
        if action_id not in self.contexts:
            raise CatalogUnavailable("execution context not found")
        return deepcopy(self.contexts[action_id])
