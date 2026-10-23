"""The one canvas view for every strategy framework.

Blueprint: strategy_bp, url_prefix="/strategy".
    strategy.index   GET  /strategy/            — every framework, one list
    strategy.canvas  GET  /strategy/<key>       — the framework in its layout
    strategy.draft   POST /strategy/<key>/draft — cited proposals (draftable only)
    strategy.accept  POST /strategy/<key>/accept — save one accepted point
    strategy.add     POST /strategy/<key>/add    — add one element to a box

Every framework (SWOT, PESTLE, VRIO, McKinsey 7S, Porter's five forces, BCG,
Ansoff, Blue Ocean) opens here, rendered from its CANVAS_TEMPLATES entry and
its layout hint. There is no second canvas view, renderer or membership rule:
placement goes through the one membership rule
(composer_export_formats.canvas_box_for_element) and the drafting engine is a
service and a panel shown for any framework with a prompt file. Reads and
writes are organisation-scoped through current_org_id, never the request body;
writes carry the one role gate.
"""
import logging

from flask import Blueprint, abort, redirect, render_template, request, url_for
from flask_login import login_required

from app.decorators import require_roles
from app.middleware.tenant_context import current_org_id
from app.services import strategy_draft_service as engine
from app.utils.api_response import error_response, success_response

logger = logging.getLogger(__name__)

strategy_bp = Blueprint("strategy", __name__, url_prefix="/strategy")

# Every strategy framework opens through this view, in the order the index
# lists them, each with the icon and one-line purpose a reader sees on its
# card. The key must be a CANVAS_TEMPLATES entry whose record reads straight
# from the organisation's own elements (record None or "elements").
_FRAMEWORKS = [
    ("swot", "layout-grid", "Strengths, weaknesses, opportunities and threats drawn from your model."),
    ("pestle", "columns-3", "The six macro-environment factors, each grounded in your model."),
    ("vrio", "table", "Read each resource across value, rarity, imitability and organisation."),
    ("mckinsey_7s", "share-2", "The seven interdependent elements, shared values at the centre."),
    ("porters_five_forces", "crosshair", "Competitive rivalry and the four forces around it."),
    ("bcg_matrix", "pie-chart", "Classify products by market share and market growth."),
    ("ansoff_matrix", "grid-2x2", "Weigh growth across existing and new products and markets."),
    ("blue_ocean_strategy", "waves", "Reshape the value curve with the four actions."),
]

# Human wording for each empty-box reason code, said in the box rather than
# hidden in an attribute, so an empty box says why it is empty and what to
# record. Keyed by reason code; the per-box line is built from the box label.
_EMPTY_REASON_LEAD = {
    "canvas_box_empty": "Nothing is recorded here yet.",
    "canvas_box_not_derived": "This box is read from the others; nothing fills it yet.",
}


def _framework_or_404(template_key):
    from app.config.archimate_viewpoints import CANVAS_TEMPLATES

    tpl = CANVAS_TEMPLATES.get(template_key)
    if tpl is None or tpl.get("record") not in (None, "elements"):
        abort(404)
    return tpl


def _empty_reason_text(zone):
    """A plain sentence for an empty box: why it is empty and what records
    fill it, built from the zone's reason code and its element types."""
    lead = _EMPTY_REASON_LEAD.get(zone.get("empty_reason"), _EMPTY_REASON_LEAD["canvas_box_empty"])
    types = zone.get("element_types") or []
    if not types:
        return lead
    readable = " or ".join(_READABLE_TYPE.get(t, t) for t in types)
    return f"{lead} Record a {readable} as “{zone['label']}” to fill it."


# Plain words for the ArchiMate types a strategy box holds, so the empty-box
# line never shows a raw type identifier like "BusinessService".
_READABLE_TYPE = {
    "Assessment": "finding",
    "Product": "product",
    "BusinessService": "business service",
    "Goal": "goal",
    "Driver": "driver",
}


@strategy_bp.route("/")
@login_required
def index():
    """Every strategy framework an architect can open, in one list."""
    from app.config.archimate_viewpoints import CANVAS_TEMPLATES

    frameworks = [
        {
            "key": key,
            "name": CANVAS_TEMPLATES[key]["name"],
            "icon": icon,
            "purpose": purpose,
            "href": url_for("strategy.canvas", template_key=key),
        }
        for key, icon, purpose in _FRAMEWORKS
        if key in CANVAS_TEMPLATES
    ]
    return render_template("strategy/index.html", frameworks=frameworks)


@strategy_bp.route("/<template_key>")
@login_required
def canvas(template_key):
    """One framework in its recognisable layout for the current organisation,
    showing the organisation's own elements already filed in each box, the
    drafting panel for a draftable framework, and the honest "not yet
    classified" count for an element-backed one."""
    tpl = _framework_or_404(template_key)
    org_id = current_org_id()
    zones = sorted(tpl["zones"], key=lambda z: z["order"])

    placed = engine.project(template_key, org_id) if org_id else {z["box_key"]: [] for z in zones}

    # Resolve each filed point's citations to display rows (name + link) so a
    # footer lands on a page with content, never a bare id. Assessment points
    # carry citations; added elements (products) do not.
    boxes = {}
    for zone in zones:
        box_key = zone["box_key"]
        entries = []
        for item in placed.get(box_key, []):
            entry = dict(item)
            # A drafted Assessment point stores its citations; resolve them.
            if "citations" in entry:
                entry["citations"] = engine.resolve_citations(entry.get("citations") or [], org_id)
            entries.append(entry)
        boxes[box_key] = entries

    draftable = engine.is_strategy_framework(template_key)
    layout = tpl.get("layout", "grid")

    # Per-zone empty-reason wording, said in the box.
    empty_reasons = {z["box_key"]: _empty_reason_text(z) for z in zones}

    return render_template(
        "strategy/canvas.html",
        framework_key=template_key,
        framework_name=tpl["name"],
        layout=layout,
        zones=zones,
        boxes=boxes,
        draftable=draftable,
        empty_reasons=empty_reasons,
        unclassified=engine.unclassified_count(template_key, org_id) if org_id else 0,
    )


@strategy_bp.route("/<template_key>/draft", methods=["POST"])
@login_required
@require_roles("admin", "architect", "business_architect", "enterprise_architect")
def draft(template_key):
    """Draft cited proposals for every box from the organisation's own model.

    Nothing is written — the response is proposals the person accepts, edits
    or rejects. Only a framework with a prompt file is draftable.
    """
    _framework_or_404(template_key)
    if not engine.is_strategy_framework(template_key):
        return error_response(
            "This framework is filled by adding its elements, not by drafting.",
            code="VALIDATION_ERROR", status_code=400,
        )
    org_id = current_org_id()
    if not org_id:
        return error_response("An organisation is required", code="FORBIDDEN", status_code=403)

    try:
        result = engine.draft(template_key, org_id)
    except ValueError as exc:
        return error_response(str(exc), code="VALIDATION_ERROR", status_code=400)
    except engine.StrategyDraftError:
        logger.warning("Strategy draft unparseable for %s", template_key)
        return error_response(
            "The model could not draft points this time. Try again.",
            code="UPSTREAM_ERROR", status_code=502,
        )
    except Exception:  # noqa: BLE001 - never leak raw provider or SQL text
        logger.exception("Strategy draft failed for %s", template_key)
        return error_response(
            "The model could not be reached. Try again.",
            code="UPSTREAM_ERROR", status_code=502,
        )

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
    """Save one accepted (or edited) point as an element in its box."""
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


@strategy_bp.route("/<template_key>/add", methods=["POST"])
@login_required
@require_roles("admin", "architect", "business_architect", "enterprise_architect")
def add(template_key):
    """Add one element to a box and file it there, then return to the canvas.

    A failed add is shown to the person as an error on the next page, never
    swallowed: the message is carried back as a query flag the view renders.
    """
    _framework_or_404(template_key)
    org_id = current_org_id()
    if not org_id:
        return error_response("An organisation is required", code="FORBIDDEN", status_code=403)

    box_key = (request.form.get("box_key") or "").strip()
    name = (request.form.get("name") or "").strip()
    element_type = (request.form.get("element_type") or "").strip() or None
    offering_level = request.form.get("offering_level")
    player = (request.form.get("player") or "").strip() or None

    try:
        engine.add_element(
            template_key, box_key, name, org_id,
            element_type=element_type, offering_level=offering_level, player=player,
        )
        from app import db

        db.session.commit()
    except ValueError as exc:
        from app import db

        db.session.rollback()
        logger.warning("Strategy add rejected on %s/%s: %s", template_key, box_key, exc)
        return redirect(url_for("strategy.canvas", template_key=template_key, add_error=str(exc)))

    return redirect(url_for("strategy.canvas", template_key=template_key, added=name))
