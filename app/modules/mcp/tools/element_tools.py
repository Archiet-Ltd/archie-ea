"""Element search and detail tools: search_elements, get_element.

search_elements wraps the canonical GET /archimate/api/elements/search
route only — none of the three duplicates named in the reuse register.
get_element wraps GET /archimate/api/elements/<id>/detail.
"""

from __future__ import annotations

from flask import current_app, session

from app.modules.ai_chat.services.architect_persona_charters import _neutralize_fence_lookalikes
from app.modules.mcp.tools import register_tool


def _call_element_search(query: str, limit: int = 30) -> dict:
    """Call the canonical element search route."""
    with current_app.test_client() as client:
        with client.session_transaction() as sess:
            sess.update(session)
        resp = client.get(f"/archimate/api/elements/search?q={query}&limit={limit}")
        return resp.get_json()


def _call_element_detail(element_id: int) -> dict:
    """Call the element detail route."""
    with current_app.test_client() as client:
        with client.session_transaction() as sess:
            sess.update(session)
        resp = client.get(f"/archimate/api/elements/{element_id}/detail")
        return resp.get_json()


@register_tool(
    name="search_elements",
    description="Search ArchiMate elements by name. Returns matching elements "
                "with their id, name, type, and layer — the identifiers the "
                "lens tools need as input.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Name substring to search for (case-insensitive)",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum results to return (default 30, max 200)",
            },
        },
        "required": ["query"],
    },
)
def search_elements(args: dict) -> dict:
    query = str(args["query"])
    limit = int(args.get("limit", 30))
    result = _call_element_search(query, limit)
    # Fence free-text fields
    if result and result.get("data"):
        for item in result["data"]:
            for key in ("name", "description"):
                if key in item and isinstance(item[key], str):
                    item[key] = _neutralize_fence_lookalikes(item[key])
    return result


@register_tool(
    name="get_element",
    description="Get the full detail for one ArchiMate element: its type, "
                "layer, description, linked solutions and capabilities.",
    input_schema={
        "type": "object",
        "properties": {
            "element_id": {
                "type": "integer",
                "description": "The ArchiMate element id to fetch",
            },
        },
        "required": ["element_id"],
    },
)
def get_element(args: dict) -> dict:
    element_id = int(args["element_id"])
    result = _call_element_detail(element_id)
    # Fence free-text fields
    if isinstance(result, dict):
        for key in ("name", "description"):
            if key in result and isinstance(result[key], str):
                result[key] = _neutralize_fence_lookalikes(result[key])
        for item in result.get("linked_solutions") or []:
            if "name" in item and isinstance(item["name"], str):
                item["name"] = _neutralize_fence_lookalikes(item["name"])
        for item in result.get("linked_capabilities") or []:
            if "name" in item and isinstance(item["name"], str):
                item["name"] = _neutralize_fence_lookalikes(item["name"])
        for item in result.get("connected_elements") or []:
            if "name" in item and isinstance(item["name"], str):
                item["name"] = _neutralize_fence_lookalikes(item["name"])
    return result