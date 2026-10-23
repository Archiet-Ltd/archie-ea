"""Routes for the strategy framework canvases (SWOT, PESTLE).

Blueprint: strategy_bp, url_prefix="/strategy".
    strategy.index           GET  /strategy/           — the two frameworks
    strategy.canvas          GET  /strategy/<key>      — the recognisable layout
    strategy.draft           POST /strategy/<key>/draft  — cited proposals
    strategy.accept          POST /strategy/<key>/accept — save one point

Reads and writes are organisation-scoped through current_org_id; the engine
(app/services/strategy_draft_service.py) owns the model call, the citation
rule and the Assessment write.
"""
import logging

from flask import Blueprint, abort, render_template, request
from flask_login import login_required

from app.decorators import require_roles
from app.middleware.tenant_context import current_org_id
from app.services import strategy_draft_service as engine
from app.utils.api_response import error_response, success_response

logger = logging.getLogger(__name__)

strategy_bp = Blueprint("strategy", __name__, url_prefix="/strategy")

# The two frameworks this module ships, in the order the index lists them.
FRAMEWORKS = ("swot", "pestle")


def _framework_or_404(template_key):
    if template_key not in FRAMEWORKS or not engine.is_strategy_framework(template_key):
        abort(404)


@strategy_bp.route("/")
@login_required
def index():
    """The two strategy frameworks an architect can open for their organisation."""
    from app.config.archimate_viewpoints import CANVAS_TEMPLATES

    frameworks = [
        {"key": key, "name": CANVAS_TEMPLATES[key]["name"]} for key in FRAMEWORKS
    ]
    return render_template("strategy/index.html", frameworks=frameworks)


@strategy_bp.route("/<template_key>")
@login_required
def canvas(template_key):
    """The SWOT 2x2 or PESTLE six-column layout for the current organisation,
    showing the points already accepted into each box with their citation."""
    _framework_or_404(template_key)
    from app.config.archimate_viewpoints import CANVAS_TEMPLATES

    org_id = current_org_id()
    tpl = CANVAS_TEMPLATES[template_key]
    zones = sorted(tpl["zones"], key=lambda z: z["order"])

    saved = engine.saved_boxes(template_key, org_id) if org_id else {z["box_key"]: [] for z in zones}
    # Resolve each saved point's citations to display rows (name + link) so the
    # footer lands on a page with content, never a bare id.
    boxes = {}
    for zone in zones:
        box_key = zone["box_key"]
        points = []
        for item in saved.get(box_key, []):
            points.append(
                {
                    "id": item["id"],
                    "point": item["point"],
                    "citations": engine.resolve_citations(item["citations"], org_id),
                }
            )
        boxes[box_key] = points

    return render_template(
        "strategy/canvas.html",
        framework_key=template_key,
        framework_name=tpl["name"],
        zones=zones,
        boxes=boxes,
    )


@strategy_bp.route("/<template_key>/draft", methods=["POST"])
@login_required
@require_roles("admin", "architect", "business_architect", "enterprise_architect")
def draft(template_key):
    """Draft cited proposals for every box from the organisation's own model.

    Nothing is written — the response is proposals the person accepts, edits
    or rejects on the page.
    """
    _framework_or_404(template_key)
    org_id = current_org_id()
    if not org_id:
        return error_response("An organisation is required", code="FORBIDDEN", status_code=403)

    try:
        result = engine.draft(template_key, org_id)
    except ValueError as exc:
        return error_response(str(exc), code="VALIDATION_ERROR", status_code=400)
    except engine.StrategyDraftError as exc:
        logger.warning("Strategy draft unparseable for %s: %s", template_key, exc)
        return error_response(f"Draft failed: {exc}", code="UPSTREAM_ERROR", status_code=502)
    except Exception as exc:  # noqa: BLE001 - surface as an error, never a fabricated draft
        logger.exception("Strategy draft failed for %s", template_key)
        return error_response(f"Draft failed: {exc}", code="UPSTREAM_ERROR", status_code=502)

    # Attach resolved citation rows so the page can show each proposal's source
    # without a second round trip.
    boxes = {}
    for box_key, points in result["boxes"].items():
        boxes[box_key] = [
            {
                "point": p["point"],
                "citation_ids": p["citations"],
                "citations": engine.resolve_citations(p["citations"], org_id),
            }
            for p in points
        ]
    return success_response({"framework": template_key, "boxes": boxes})


@strategy_bp.route("/<template_key>/accept", methods=["POST"])
@login_required
@require_roles("admin", "architect", "business_architect", "enterprise_architect")
def accept(template_key):
    """Save one accepted (or edited) point as an Assessment element in its box."""
    _framework_or_404(template_key)
    org_id = current_org_id()
    if not org_id:
        return error_response("An organisation is required", code="FORBIDDEN", status_code=403)

    payload = request.get_json(silent=True) or {}
    box_key = payload.get("box_key")
    point = payload.get("point")
    citations = payload.get("citations") or []

    try:
        saved = engine.accept_point(template_key, box_key, point, citations, org_id)
    except ValueError as exc:
        return error_response(str(exc), code="VALIDATION_ERROR", status_code=400)

    from app import db

    db.session.commit()
    saved["citations"] = engine.resolve_citations(saved["citations"], org_id)
    return success_response(saved)
