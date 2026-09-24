"""Canvas tools: list_canvases, get_canvas.

list_canvases wraps the Business Model Canvas list and Business Case list.
get_canvas wraps the Business Model Canvas detail and Business Case detail.
"""

from __future__ import annotations

from flask import current_app, session

from app.modules.ai_chat.services.architect_persona_charters import _neutralize_fence_lookalikes
from app.modules.mcp.tools import register_tool


def _call_canvas_list() -> dict:
    """Call the Business Model Canvas list and Business Case list routes."""
    with current_app.test_client() as client:
        with client.session_transaction() as sess:
            sess.update(session)

        # Business Model Canvases
        bmc_resp = client.get("/business-model/")
        # Business Cases
        bc_resp = client.get("/business-case/")

    return {
        "business_model_canvases": _extract_canvas_list(bmc_resp),
        "business_cases": _extract_business_case_list(bc_resp),
    }


def _extract_canvas_list(resp) -> list:
    """Extract canvas data from the rendered template response."""
    # The index page renders HTML; we need to query the service directly
    # since there's no JSON API for listing canvases.
    from app.modules.business_model_canvas.service import list_canvases
    canvases = list_canvases()
    return [
        {
            "id": c.id,
            "name": _neutralize_fence_lookalikes(c.name or ""),
            "description": _neutralize_fence_lookalikes(c.description or ""),
            "operating_model_type": c.operating_model_type,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        }
        for c in canvases
    ]


def _extract_business_case_list(resp) -> list:
    """Extract business case data from the rendered template response."""
    from app.modules.business_case.service import list_business_cases
    cases = list_business_cases()
    return [
        {
            "id": bc.id,
            "title": _neutralize_fence_lookalikes(bc.title or ""),
            "status": bc.status,
            "updated_at": bc.updated_at.isoformat() if bc.updated_at else None,
        }
        for bc in cases
    ]


@register_tool(
    name="list_canvases",
    description="List all Business Model Canvases and Business Cases "
                "for the current organisation.",
    input_schema={
        "type": "object",
        "properties": {},
    },
)
def list_canvases(args: dict) -> dict:
    return _call_canvas_list()


@register_tool(
    name="get_canvas",
    description="Get the full detail of one Business Model Canvas or Business Case.",
    input_schema={
        "type": "object",
        "properties": {
            "canvas_type": {
                "type": "string",
                "description": "Either 'business_model' or 'business_case'",
                "enum": ["business_model", "business_case"],
            },
            "canvas_id": {
                "type": "integer",
                "description": "The canvas or business case id",
            },
        },
        "required": ["canvas_type", "canvas_id"],
    },
)
def get_canvas(args: dict) -> dict:
    canvas_type = str(args["canvas_type"])
    canvas_id = int(args["canvas_id"])

    if canvas_type == "business_model":
        from app.modules.business_model_canvas.service import get_canvas_or_none
        canvas = get_canvas_or_none(canvas_id)
        if canvas is None:
            return {"success": False, "error": {"code": "NOT_FOUND", "message": "Business Model Canvas not found"}}
        data = canvas.to_dict()
        # Fence free-text fields
        for key in ("name", "description"):
            if key in data and isinstance(data[key], str):
                data[key] = _neutralize_fence_lookalikes(data[key])
        for block_key, block_content in (data.get("blocks") or {}).items():
            if isinstance(block_content, str):
                data["blocks"][block_key] = _neutralize_fence_lookalikes(block_content)
        return {"success": True, "data": data}

    elif canvas_type == "business_case":
        from app.modules.business_case.service import get_business_case_or_none
        case = get_business_case_or_none(canvas_id)
        if case is None:
            return {"success": False, "error": {"code": "NOT_FOUND", "message": "Business Case not found"}}
        data = case.to_dict()
        # Fence free-text fields
        for key in ("title", "description", "problem_statement", "options_considered",
                     "recommended_option", "expected_benefits", "key_risks"):
            if key in data and isinstance(data[key], str):
                data[key] = _neutralize_fence_lookalikes(data[key])
        return {"success": True, "data": data}

    return {"success": False, "error": {"code": "INVALID_PARAMETER",
            "message": f"Unknown canvas_type: {canvas_type}"}}