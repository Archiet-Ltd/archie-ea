"""The six lens tools: ask_impact, ask_strategy, ask_portfolio,
ask_programme, ask_risk, ask_accountability.

Each wraps the corresponding GET /api/v1/intelligence/<lens>/<element_id>
REST endpoint. The MCP layer imports no model and no service — it calls
the existing REST routes through Flask's test client.
"""

from __future__ import annotations

from flask import current_app

from app.modules.ai_chat.services.architect_persona_charters import _neutralize_fence_lookalikes
from app.modules.mcp.tools import register_tool


def _call_intelligence_route(lens: str, element_id: int) -> dict:
    """Call a GET /api/v1/intelligence/<lens>/<element_id> route in-process.

    Uses Flask's test client so the request goes through the full middleware
    stack (tenant context, tenant isolation, login_required) with the
    current_user already set by the MCP blueprint's _authenticate_request.
    """
    with current_app.test_client() as client:
        # Pass the current session cookie so the route sees the authenticated user
        from flask import session
        with client.session_transaction() as sess:
            sess.update(session)

        resp = client.get(f"/api/v1/intelligence/{lens}/{element_id}")
        data = resp.get_json()

    # Apply fencing to free-text fields in the response
    if data and data.get("success") and data.get("data"):
        _fence_response_text(data["data"])

    return data


def _fence_response_text(payload: dict) -> None:
    """Apply _neutralize_fence_lookalikes to every free-text field in the payload.

    Only fields that are strings and not known to be enums or numbers are fenced.
    """
    if not isinstance(payload, dict):
        return

    # Fields that are safe (enums, numbers, ids, booleans) — skip fencing
    _safe_keys = {
        "id", "element_id", "risk_id", "work_package_id", "initiative_id",
        "application_component_id", "organization_unit_id", "depth",
        "explicit_count", "derived_count", "stale_count", "latency_ms",
        "likelihood", "impact", "risk_score", "progress_percentage",
        "completion_percentage", "cost_variance_pct", "budget_variance_pct",
        "target_value", "actual_value", "kind", "confidence", "stale",
        "derived_id", "engine_version", "chain", "chain_elements",
        "is_overdue", "capacity_not_available", "unresolved",
        "organization_id", "ratio", "sample_count",
    }

    for key, value in list(payload.items()):
        if isinstance(value, str) and key not in _safe_keys:
            payload[key] = _neutralize_fence_lookalikes(value)
        elif isinstance(value, dict):
            _fence_response_text(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _fence_response_text(item)


@register_tool(
    name="ask_impact",
    description="Ask what stops if an element fails, and who owns it. "
                "Returns the cross-layer impact chain for an ArchiMate element.",
    input_schema={
        "type": "object",
        "properties": {
            "element_id": {
                "type": "integer",
                "description": "The ArchiMate element id to query",
            },
        },
        "required": ["element_id"],
    },
)
def ask_impact(args: dict) -> dict:
    element_id = int(args["element_id"])
    return _call_intelligence_route("impact", element_id)


@register_tool(
    name="ask_strategy",
    description="Ask what strategic initiatives an element is linked to, "
                "and how each is tracking against its budget.",
    input_schema={
        "type": "object",
        "properties": {
            "element_id": {
                "type": "integer",
                "description": "The ArchiMate element id to query",
            },
        },
        "required": ["element_id"],
    },
)
def ask_strategy(args: dict) -> dict:
    element_id = int(args["element_id"])
    return _call_intelligence_route("strategy", element_id)


@register_tool(
    name="ask_portfolio",
    description="Ask which application component an element maps to, "
                "for rationalization and duplicate detection.",
    input_schema={
        "type": "object",
        "properties": {
            "element_id": {
                "type": "integer",
                "description": "The ArchiMate element id to query",
            },
        },
        "required": ["element_id"],
    },
)
def ask_portfolio(args: dict) -> dict:
    element_id = int(args["element_id"])
    return _call_intelligence_route("portfolio", element_id)


@register_tool(
    name="ask_programme",
    description="Ask what work packages an element is part of, "
                "whether each is on time and on budget, and what each touches.",
    input_schema={
        "type": "object",
        "properties": {
            "element_id": {
                "type": "integer",
                "description": "The ArchiMate element id to query",
            },
        },
        "required": ["element_id"],
    },
)
def ask_programme(args: dict) -> dict:
    element_id = int(args["element_id"])
    return _call_intelligence_route("programme", element_id)


@register_tool(
    name="ask_risk",
    description="Ask what risks threaten an element, and what each risk touches.",
    input_schema={
        "type": "object",
        "properties": {
            "element_id": {
                "type": "integer",
                "description": "The ArchiMate element id to query",
            },
        },
        "required": ["element_id"],
    },
)
def ask_risk(args: dict) -> dict:
    element_id = int(args["element_id"])
    return _call_intelligence_route("risk", element_id)


@register_tool(
    name="ask_accountability",
    description="Ask who is accountable for an element. "
                "Currently returns an honest empty state — the ownership "
                "reader is not yet built.",
    input_schema={
        "type": "object",
        "properties": {
            "element_id": {
                "type": "integer",
                "description": "The ArchiMate element id to query",
            },
        },
        "required": ["element_id"],
    },
)
def ask_accountability(args: dict) -> dict:
    element_id = int(args["element_id"])
    return _call_intelligence_route("accountability", element_id)