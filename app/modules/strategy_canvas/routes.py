"""Routes for the Strategy Canvas module.

Blueprint: strategy_canvas_bp, url_prefix="/strategy-canvas".

    strategy_canvas.index   — the three frameworks, each a link to its canvas
    strategy_canvas.view    — one framework in its recognisable layout
    strategy_canvas.add     — add an element to a box (architect write)
"""

import logging

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import login_required

# The write path reuses the route-level role gate the other canvas modules
# use, so adding an element follows the same authority as creating a business
# case or a business-model canvas — no hand-rolled role check.
from app.decorators import require_roles

from app.config.archimate_viewpoints import CANVAS_TEMPLATES
from app.services import strategy_canvas_service as canvas_service

logger = logging.getLogger(__name__)

strategy_canvas_bp = Blueprint(
    "strategy_canvas", __name__, url_prefix="/strategy-canvas"
)

# Which ArchiMate layer each element type lives in, so a placed element links
# to its detail page under the right layer segment.
_LAYER_FOR_TYPE = {
    "Product": "business",
    "BusinessService": "business",
    "Assessment": "motivation",
    "Goal": "motivation",
}

# The three element-backed frameworks this module owns, in the order the
# index lists them. Each entry carries the icon and one-line purpose a reader
# sees on the index card.
_CANVASES = [
    ("bcg_matrix", "pie-chart",
     "Classify the organisation's products by market share and market growth."),
    ("ansoff_matrix", "grid-2x2",
     "Weigh growth moves across existing and new products and markets."),
    ("blue_ocean_strategy", "waves",
     "Reshape the value curve away from a crowded market with the four actions."),
]

# The recognisable shape each framework draws its boxes in. A 2x2 names the
# two axes and the four quadrants' box_keys clockwise from top-left; the value
# form names the curve box and the four-action boxes.
_LAYOUTS = {
    "bcg_matrix": {
        "shape": "matrix",
        "x_axis": "Relative market share",
        "x_high": "High",
        "x_low": "Low",
        "y_axis": "Market growth",
        "y_high": "High",
        "y_low": "Low",
        # top-left, top-right, bottom-left, bottom-right
        "cells": ["stars", "question_marks", "cash_cows", "dogs"],
    },
    "ansoff_matrix": {
        "shape": "matrix",
        "x_axis": "Products",
        "x_high": "Existing",
        "x_low": "New",
        "y_axis": "Markets",
        "y_high": "Existing",
        "y_low": "New",
        "cells": ["market_penetration", "product_development",
                  "market_development", "diversification"],
    },
    "blue_ocean_strategy": {
        "shape": "value",
        "curve_box": "value_curve",
        "factor_box": "competing_factors",
        "action_boxes": ["eliminate", "reduce", "raise", "create"],
    },
}


@strategy_canvas_bp.route("/")
@login_required
def index():
    """List the three strategy frameworks, each a link to its canvas."""
    canvases = [
        {
            "key": key,
            "name": CANVAS_TEMPLATES[key]["name"],
            "icon": icon,
            "purpose": purpose,
            "href": url_for("strategy_canvas.view", canvas_key=key),
        }
        for key, icon, purpose in _CANVASES
        if key in CANVAS_TEMPLATES
    ]
    return render_template("strategy_canvas/index.html", canvases=canvases)


@strategy_canvas_bp.route("/<canvas_key>")
@login_required
def view(canvas_key):
    """Open one framework in its recognisable layout for the signed-in
    architect, placing the organisation's own elements in each box."""
    if not canvas_service.is_strategy_canvas(canvas_key):
        return render_template("strategy_canvas/not_found.html", canvas_key=canvas_key), 404

    tpl = CANVAS_TEMPLATES[canvas_key]
    zones = {z["box_key"]: z for z in tpl["zones"]}
    placed = canvas_service.project(canvas_key)
    unclassified = canvas_service.unclassified_count(canvas_key)

    # Each placed element links to its own detail page, which renders the
    # element's fields and relationships — a real page with content, never a
    # dead or self-referential link. The detail route keys off the element's
    # ArchiMate layer, so map each element's type to its layer here.
    for entries in placed.values():
        for entry in entries:
            layer = _LAYER_FOR_TYPE.get(entry.get("type"))
            if layer and "id" in entry:
                entry["detail_href"] = url_for(
                    "archimate_crud.detail_element",
                    layer=layer,
                    element_type=entry["type"],
                    element_id=entry["id"],
                )

    curve = None
    if _LAYOUTS[canvas_key]["shape"] == "value":
        curve = _build_value_curve(placed.get("value_curve", []))

    return render_template(
        "strategy_canvas/view.html",
        canvas_key=canvas_key,
        canvas_name=tpl["name"],
        zones=zones,
        placed=placed,
        layout=_LAYOUTS[canvas_key],
        unclassified=unclassified,
        curve=curve,
    )


# Value-curve chart geometry. The chart is drawn as an inline SVG from these
# numbers — one polyline per player across the competing factors — so it needs
# no chart library (there is no shared line-chart component to reuse). A factor
# with no offering level leaves a gap in that player's line rather than a
# fabricated point at zero.
_CURVE_WIDTH = 760
_CURVE_HEIGHT = 240
_CURVE_PAD_X = 40
_CURVE_PAD_TOP = 16
_CURVE_PAD_BOTTOM = 56


def _build_value_curve(points):
    """Shape the value-curve points into axis ticks and one series per player.

    ``points`` is the projection's value_curve list (factor, player,
    offering_level). Returns None when there is nothing to plot, so the view
    shows the empty reason instead of an axis with no line.
    """
    factors = []
    for point in points:
        if point["factor"] not in factors:
            factors.append(point["factor"])
    if not factors:
        return None

    plot_w = _CURVE_WIDTH - 2 * _CURVE_PAD_X
    plot_h = _CURVE_HEIGHT - _CURVE_PAD_TOP - _CURVE_PAD_BOTTOM
    step = plot_w / (len(factors) - 1) if len(factors) > 1 else 0

    def _x(index):
        return _CURVE_PAD_X + (index * step if len(factors) > 1 else plot_w / 2)

    def _y(level):
        return _CURVE_PAD_TOP + plot_h - (level / 10.0) * plot_h

    axis_factors = [
        {"label": factor, "x": round(_x(i), 1)} for i, factor in enumerate(factors)
    ]

    series_by_player = {}
    for point in points:
        player = point["player"] or "This organisation"
        series_by_player.setdefault(player, {})[point["factor"]] = point["offering_level"]

    series = []
    for player, levels in series_by_player.items():
        plotted = []
        for i, factor in enumerate(factors):
            level = levels.get(factor)
            if level is None:
                continue
            plotted.append({"x": round(_x(i), 1), "y": round(_y(level), 1), "level": level,
                            "factor": factor})
        if plotted:
            series.append({"player": player, "points": plotted})

    y_ticks = [{"level": lvl, "y": round(_y(lvl), 1)} for lvl in (0, 5, 10)]

    return {
        "width": _CURVE_WIDTH,
        "height": _CURVE_HEIGHT,
        "factors": axis_factors,
        "series": series,
        "y_ticks": y_ticks,
        "baseline_y": round(_y(0), 1),
    }


@strategy_canvas_bp.route("/<canvas_key>/add", methods=["POST"])
@login_required
@require_roles("admin", "architect", "business_architect")
def add(canvas_key):
    """Add an element to a box and file it there, then return to the canvas."""
    if not canvas_service.is_strategy_canvas(canvas_key):
        return render_template("strategy_canvas/not_found.html", canvas_key=canvas_key), 404

    box_key = (request.form.get("box_key") or "").strip()
    name = (request.form.get("name") or "").strip()
    element_type = (request.form.get("element_type") or "").strip() or None
    offering_level = request.form.get("offering_level")
    player = (request.form.get("player") or "").strip() or None

    try:
        canvas_service.add_element(
            canvas_key,
            box_key,
            name,
            element_type=element_type,
            offering_level=offering_level,
            player=player,
        )
        from app import db

        db.session.commit()
    except ValueError as exc:
        logger.warning("Invalid strategy canvas add on %s/%s: %s", canvas_key, box_key, exc)

    return redirect(url_for("strategy_canvas.view", canvas_key=canvas_key))
