"""Routes for the Business Model Canvas + Operating Model module.

Blueprint: business_model_bp, url_prefix="/business-model".
Index endpoint (linked from the sidebar by the orchestrator post-merge):
    business_model.index
"""

import logging

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for
from flask_login import login_required

# Destructive and mutating routes were guarded by @login_required only, so any
# authenticated user could delete another user's records. Matches the gating
# already used by app/modules/capabilities/routes/enterprise_crud_routes.py.
from app.decorators import require_roles

from app import db
from app.models.business_model import OPERATING_MODEL_TYPES
from app.models.price_plan import BILLING_PERIODS
from app.utils.api_response import error_response, not_found_response, success_response

from werkzeug.exceptions import HTTPException

from . import model_service, service

logger = logging.getLogger(__name__)

EDIT_ROLES = ("admin", "architect", "business_architect")

business_model_bp = Blueprint(
    "business_model", __name__, url_prefix="/business-model"
)


@business_model_bp.route("/")
@login_required
def index():
    """List all Business Model Canvases for the current tenant."""
    canvases = service.list_canvases()
    return render_template(
        "business_model/index.html",
        canvases=canvases,
        operating_model_types=OPERATING_MODEL_TYPES,
    )


@business_model_bp.route("/create", methods=["POST"])
@login_required
@require_roles(*EDIT_ROLES)
def create():
    """Create a new canvas and redirect to its detail/canvas view."""
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip() or None
    operating_model_type = request.form.get("operating_model_type") or None

    try:
        canvas = service.create_canvas(
            name=name or "Untitled Canvas",
            description=description,
            operating_model_type=operating_model_type,
        )
    except ValueError as exc:
        logger.warning("Invalid canvas create payload: %s", exc)
        return redirect(url_for("business_model.index"))

    return redirect(url_for("business_model.detail", canvas_id=canvas.id))


def _can_edit():
    """Whether this person may change the canvas: the same role gate the
    write routes carry, asked without running a route."""
    try:
        require_roles(*EDIT_ROLES)(lambda: True)()
    except HTTPException:
        return False
    return True


def _note_entry(canvas, box_key, el, editable):
    note = {
        "id": el.id, "text": el.name, "type": el.type, "box": box_key,
        "href": model_service.element_href(el),
    }
    if editable:
        note["remove_url"] = url_for("business_model.remove_entry", canvas_id=canvas.id, element_id=el.id)
    return note


def _canvas_boxes(canvas):
    """The notes of every box, read from the model: elements for seven boxes,
    price plans for Revenue Streams and cost facts for Cost Structure."""
    editable = _can_edit()
    boxes = {key.replace("_", "-"): [_note_entry(canvas, key, el, editable) for el in model_service.entries(canvas, key)]
             for key in model_service.element_boxes()}
    elements = {el.id: el for el in model_service.entries(canvas, model_service.PRICEABLE_BOX)}
    plan_href = {eid: model_service.element_href(el) for eid, el in elements.items()}
    boxes["revenue-streams"] = [
        {
            "id": plan.id, "box": "revenue_streams",
            "text": f"{elements[plan.element_id].name}: {plan.name}, {plan.price:,.2f} {plan.currency} per {plan.billing_period}",
            "href": plan_href[plan.element_id],
            **({"remove_url": url_for("business_model.remove_price", canvas_id=canvas.id, plan_id=plan.id)} if editable else {}),
        }
        for plan in model_service.price_plans(canvas) if plan.element_id in elements
    ]
    lines = model_service.cost_lines(canvas)
    boxes["cost-structure"] = [
        {
            "text": f"{line['name']}: {line['amount']:,.0f} {line['currency']} a year",
            "href": url_for("unified_applications.application_detail", id=line["id"]),
        }
        for line in lines[:8]
    ]
    return boxes, len(lines)


@business_model_bp.route("/<int:canvas_id>")
@login_required
def detail(canvas_id):
    """The 9-box Business Model Canvas, drawn from the organisation's model."""
    canvas = service.get_canvas_or_none(canvas_id)
    if canvas is None:
        return render_template("business_model/not_found.html", canvas_id=canvas_id), 404

    boxes, cost_line_count = _canvas_boxes(canvas)
    zones = model_service.zones()
    pickers = {
        key: [{"id": el.id, "name": el.name, "type": el.type} for el in model_service.candidates(canvas, key)]
        for key in model_service.element_boxes()
    }
    vision = None
    if canvas.vision_element_id:
        from app.models.archimate_core import ArchiMateElement

        vision = ArchiMateElement.query.filter_by(
            id=canvas.vision_element_id, organization_id=canvas.organization_id
        ).first()
    return render_template(
        "business_model/detail.html",
        canvas=canvas,
        boxes=boxes,
        zones=zones,
        pickers=pickers,
        cost_line_count=cost_line_count,
        vision=vision,
        vision_href=model_service.element_href(vision) if vision else None,
        can_edit=_can_edit(),
        operating_model_types=OPERATING_MODEL_TYPES,
        billing_periods=BILLING_PERIODS,
        priceable=boxes["value-propositions"],
    )


@business_model_bp.route("/<int:canvas_id>/value-proposition")
@login_required
def value_proposition(canvas_id):
    """The Value Proposition Canvas over the same segment and value elements."""
    canvas = service.get_canvas_or_none(canvas_id)
    if canvas is None:
        return render_template("business_model/not_found.html", canvas_id=canvas_id), 404
    from app.modules.business_model_canvas import vpc_service

    return render_template(
        "business_model/value_proposition.html", canvas=canvas, boxes=vpc_service.boxes(canvas),
        segments=vpc_service.segments(canvas),
    )


def _back(canvas_id, box=None):
    return redirect(url_for("business_model.detail", canvas_id=canvas_id) + (f"#box-{box}" if box else ""))


def _canvas_write(canvas_id, action):
    canvas = service.get_canvas_or_none(canvas_id)
    if canvas is None:
        return None, not_found_response("Business Model Canvas")
    try:
        action(canvas)
    except model_service.CanvasError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return canvas, None


@business_model_bp.route("/<int:canvas_id>/entries/add", methods=["POST"])
@login_required
@require_roles(*EDIT_ROLES)
def add_entry(canvas_id):
    box = request.form.get("box", "")
    canvas, missing = _canvas_write(
        canvas_id,
        lambda c: model_service.add_entry(
            c, box, element_id=request.form.get("element_id") or None, name=request.form.get("name"),
        ),
    )
    return missing or _back(canvas_id, box.replace("_", "-"))


@business_model_bp.route("/<int:canvas_id>/entries/<int:element_id>/rename", methods=["POST"])
@login_required
@require_roles(*EDIT_ROLES)
def rename_entry(canvas_id, element_id):
    box = request.form.get("box", "")
    canvas, missing = _canvas_write(
        canvas_id, lambda c: model_service.rename_entry(c, box, element_id, request.form.get("name"))
    )
    return missing or _back(canvas_id, box.replace("_", "-"))


@business_model_bp.route("/<int:canvas_id>/entries/<int:element_id>/remove", methods=["POST"])
@login_required
@require_roles(*EDIT_ROLES)
def remove_entry(canvas_id, element_id):
    box = request.form.get("box", "")
    canvas, missing = _canvas_write(canvas_id, lambda c: model_service.remove_entry(c, box, element_id))
    return missing or _back(canvas_id, box.replace("_", "-"))


@business_model_bp.route("/<int:canvas_id>/prices/add", methods=["POST"])
@login_required
@require_roles(*EDIT_ROLES)
def add_price(canvas_id):
    form = request.form
    canvas, missing = _canvas_write(
        canvas_id,
        lambda c: model_service.add_price_plan(
            c, form.get("element_id"), form.get("name"), form.get("price"),
            form.get("currency"), form.get("billing_period"), form.get("pricing_mechanism"),
        ),
    )
    return missing or _back(canvas_id, "revenue-streams")


@business_model_bp.route("/<int:canvas_id>/prices/<int:plan_id>/remove", methods=["POST"])
@login_required
@require_roles(*EDIT_ROLES)
def remove_price(canvas_id, plan_id):
    canvas, missing = _canvas_write(canvas_id, lambda c: model_service.remove_price_plan(c, plan_id))
    return missing or _back(canvas_id, "revenue-streams")


@business_model_bp.route("/<int:canvas_id>/export", methods=["GET"])
@login_required
def export_canvas(canvas_id):
    """Export this canvas through the existing saved-viewpoint formats
    (mermaid, lucid, archi) over the record's saved diagram, with every empty
    box's reason named in the file.

    Query parameter: format (mermaid|lucid|archi, default mermaid).
    """
    canvas = service.get_canvas_or_none(canvas_id)
    if canvas is None:
        return not_found_response("Business Model Canvas")

    fmt = request.args.get("format", "mermaid")
    if fmt not in ("mermaid", "lucid", "archi"):
        return error_response(f"Unsupported format: {fmt}", code="VALIDATION_ERROR", status_code=400)

    from app.services.composer_export_formats import (
        export_canvas_viewpoint,
        resolve_canvas_saved_diagram_id,
    )

    saved_diagram_id = resolve_canvas_saved_diagram_id(canvas, "business_model_canvas")
    body, mimetype, ext = export_canvas_viewpoint(
        "business_model_canvas", saved_diagram_id, fmt,
        name=canvas.name or "Business Model Canvas",
    )
    filename = f"business-model-canvas-{canvas_id}.{ext}"
    return Response(
        body, status=200, mimetype=mimetype,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@business_model_bp.route("/<int:canvas_id>/update", methods=["POST"])
@login_required
@require_roles(*EDIT_ROLES)
def update_canvas(canvas_id):
    """Update canvas name / description / operating model type (JSON API)."""
    canvas = service.get_canvas_or_none(canvas_id)
    if canvas is None:
        return not_found_response("Business Model Canvas")

    payload = request.get_json(silent=True) or request.form
    name = payload.get("name")
    description = payload.get("description")
    operating_model_type = payload.get("operating_model_type")

    try:
        canvas = service.update_canvas_meta(
            canvas,
            name=name,
            description=description,
            operating_model_type=operating_model_type,
        )
    except ValueError as exc:
        return error_response(str(exc), code="VALIDATION_ERROR", status_code=400)

    if not request.is_json:
        return _back(canvas_id)
    return success_response(canvas.to_dict())


@business_model_bp.route("/<int:canvas_id>/delete", methods=["POST"])
@login_required
# Same class of bug as DEF-075 on business_case.delete (Capgemini dry-run):
# this required "admin" while create/update/save_block above accept
# admin/architect/business_architect, so the business_architect who created a
# canvas got a 403 (full-page error, since this route redirects rather than
# returning JSON) trying to delete their own record. Same roles as
# create/update/save_block.
@require_roles(*EDIT_ROLES)
def delete_canvas(canvas_id):
    """Delete a canvas."""
    canvas = service.get_canvas_or_none(canvas_id)
    if canvas is None:
        return not_found_response("Business Model Canvas")

    service.delete_canvas(canvas)
    return redirect(url_for("business_model.index"))


@business_model_bp.route("/api/list")
@login_required
def api_list():
    """JSON list of all Business Model Canvases for the current tenant."""
    canvases = service.list_canvases()
    return success_response([
        {
            "id": c.id,
            "name": c.name or "",
            "description": c.description or "",
            "operating_model_type": c.operating_model_type,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        }
        for c in canvases
    ])


@business_model_bp.route("/api/<int:canvas_id>")
@login_required
def api_detail(canvas_id):
    """JSON detail of one Business Model Canvas."""
    canvas = service.get_canvas_or_none(canvas_id)
    if canvas is None:
        return not_found_response("Business Model Canvas")
    return success_response(canvas.to_dict())


# Import AI block-draft route — adds POST /api/<id>/ai-draft-block to this
# blueprint (side-effect import), matching the
# app/modules/architecture/routes/arb_review_ai_routes.py pattern.
from app.modules.business_model_canvas import ai_routes  # noqa: F401, E402  # dead-code-ok
