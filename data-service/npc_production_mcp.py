"""MCP adapter for the local NPC Gen planning service."""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations


BASE_URL = os.getenv("NPC_GEN_URL", "http://127.0.0.1:8030").rstrip("/")
mcp = FastMCP("ProjectBS NPC Production")

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
VERSIONED_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)


def _request(path: str, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{BASE_URL}{path}", data=body, method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"NPC Gen API {exc.code}: {detail}") from exc
    except URLError as exc:
        raise ConnectionError(f"NPC Gen API에 연결할 수 없습니다: {BASE_URL}") from exc


@mcp.tool(structured_output=True, annotations=READ_ONLY)
def master_concept_get() -> dict[str, Any]:
    """Get the current master concept document and revision before any planning update."""
    return _request("/api/concept")


@mcp.tool(structured_output=True, annotations=VERSIONED_WRITE)
def master_concept_update(expected_revision: int, note: str, document: dict[str, Any]) -> dict[str, Any]:
    """Save a validated full master concept as a new revision. Read first and preserve unrelated fields."""
    return _request("/api/concept", "PUT", {
        "expectedRevision": expected_revision, "note": note, "document": document,
    })


@mcp.tool(structured_output=True, annotations=READ_ONLY)
def master_concept_revisions() -> dict[str, Any]:
    """List saved master concept revisions and their change notes."""
    return _request("/api/revisions")


@mcp.tool(structured_output=True, annotations=READ_ONLY)
def story_get() -> dict[str, Any]:
    """Get the current story library used by NPC production."""
    return _request("/api/story")


@mcp.tool(structured_output=True, annotations=VERSIONED_WRITE)
def story_update(expected_revision: int, note: str, document: dict[str, Any]) -> dict[str, Any]:
    """Save a validated full story library as a new revision."""
    return _request("/api/story", "PUT", {
        "expectedRevision": expected_revision, "note": note, "document": document,
    })


@mcp.tool(structured_output=True, annotations=READ_ONLY)
def npc_catalog_get() -> dict[str, Any]:
    """Get the current NPC catalog, profiles, shared state, and revision."""
    return _request("/api/npcs")


@mcp.tool(structured_output=True, annotations=READ_ONLY)
def npc_get(npc_id: str) -> dict[str, Any]:
    """Get one NPC with its detailed combat and numeric profile."""
    return _request(f"/api/npcs/{quote(npc_id, safe='')}")


@mcp.tool(structured_output=True, annotations=VERSIONED_WRITE)
def npc_catalog_update(expected_revision: int, note: str, document: dict[str, Any]) -> dict[str, Any]:
    """Save the validated full NPC catalog as a new revision. Read first and preserve unrelated NPCs."""
    return _request("/api/npcs", "PUT", {
        "expectedRevision": expected_revision, "note": note, "document": document,
    })


@mcp.tool(structured_output=True, annotations=READ_ONLY)
def production_knowledge_overview() -> dict[str, Any]:
    """Get bounded ProjectBS knowledge categories before selecting details."""
    return _request("/api/knowledge/overview")


@mcp.tool(structured_output=True, annotations=READ_ONLY)
def production_knowledge_category(category_id: str) -> dict[str, Any]:
    """Get the small entry catalog for one selected knowledge category."""
    return _request(f"/api/knowledge/categories/{quote(category_id, safe='')}")


@mcp.tool(structured_output=True, annotations=READ_ONLY)
def production_knowledge_entry(entry_id: str) -> dict[str, Any]:
    """Read one explicitly selected knowledge entry and its structured execution data."""
    return _request(f"/api/knowledge/entries/{quote(entry_id, safe='')}")


@mcp.tool(structured_output=True, annotations=READ_ONLY)
def production_mcp_capabilities() -> dict[str, Any]:
    """Get registered execution capabilities, including image versus video-to-frame policies."""
    return _request("/api/knowledge/mcp-capabilities")


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
