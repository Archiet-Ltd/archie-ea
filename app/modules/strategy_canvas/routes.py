"""Routes for the strategy canvas module.

Blueprint: strategy_canvas_bp, url_prefix="/strategy-canvas".

Three canvases — VRIO, McKinsey 7S and Porter's five forces — each opening in
its recognisable layout for a signed-in architect, showing the organisation's
own elements placed in the right box.
"""

import logging

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import login_required

# The one role helper for permission checks (the same decorator the
# business-model and business-case canvases use for their write routes).
from app.decorators import require_roles

from . import service

logger = logging.getLogger(__name__)

strategy_canvas_bp = Blueprint(
    "strategy_canvas", __name__, url_prefix="/strategy-canvas"
)

# Each template key renders through its own recognisable-layout template.
_LAYOUT_TEMPLATES = {
    "vrio": "strategy_canvas/vrio.html",
    "mckinsey_7s": "strategy_canvas/mckinsey_7s.html",
    "porters_five_forces": "strategy_canvas/porters_five_forces.html",
}


@strategy_canvas_bp.route("/")
@login_required
def index():
    """The three strategy canvases, each a link to its own layout."""
    from app.config.archimate_viewpoints import CANVAS_TEMPLATES

    canvases = [
        {"key": key, "name": CANVAS_TEMPLATES[key]["name"]}
        for key in _LAYOUT_TEMPLATES
        if key in CANVAS_TEMPLATES
    ]
    return render_template("strategy_canvas/index.html", canvases=canvases)


@strategy_canvas_bp.route("/<template_key>")
@login_required
def detail(template_key):
    """One strategy canvas in its recognisable layout, with the tenant's
    elements projected into their boxes."""
    tpl = service.get_template_or_none(template_key)
    if tpl is None or template_key not in _LAYOUT_TEMPLATES:
        return render_template("strategy_canvas/not_found.html", template_key=template_key), 404

    from app.services.strategy_canvas_projection import project_canvas, unclassified_count

    projected = project_canvas(template_key)
    zones_by_key = {zone["box_key"]: zone for zone in projected}

    return render_template(
        _LAYOUT_TEMPLATES[template_key],
        template_key=template_key,
        template_name=tpl["name"],
        zones=projected,
        zones_by_key=zones_by_key,
        unclassified=unclassified_count(template_key),
    )


@strategy_canvas_bp.route("/<template_key>/add", methods=["POST"])
@login_required
@require_roles("admin", "architect", "business_architect")
def add_element(template_key):
    """Add an element to one box of a strategy canvas, then return to the
    canvas so it shows in that box after reload."""
    if template_key not in _LAYOUT_TEMPLATES:
        return render_template("strategy_canvas/not_found.html", template_key=template_key), 404

    box_key = (request.form.get("box_key") or "").strip()
    name = request.form.get("name") or ""
    try:
        service.add_element_to_box(template_key, box_key, name)
    except ValueError as exc:
        logger.warning("Invalid strategy canvas add: %s", exc)

    return redirect(url_for("strategy_canvas.detail", template_key=template_key))
