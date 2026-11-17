"""Capability Coverage page — deterministic genome slice, org-scoped read.

One read route + one page. The presenter clicks "Generate coverage matrix";
the server builds the business-layer genome slice for the current tenant
(zero LLM), the deterministic Jinja emitter renders it to an HTML heatmap, and
the page shows it with element-level provenance on every populated cell.

No write path — pure read + deterministic render. Support is recorded on an application's
Capabilities tab, on an element page with Add relationship, or in the AI chat; this page only
reads it and says how much it read.
"""
from __future__ import annotations

import logging

from flask import Blueprint, g, render_template
from flask_login import current_user, login_required
from markupsafe import Markup

from app.modules.genome.emit.coverage_matrix import emit_coverage_matrix_html
from app.services.capability_support import format_generated_at, support_summary
from app.modules.genome.services.coverage_slice import (
    SliceProvenanceError,
    build_coverage_slice,
)

logger = logging.getLogger(__name__)

genome_coverage_bp = Blueprint(
    "genome_coverage",
    __name__,
    url_prefix="/genome/coverage",
    template_folder="../templates",
)


def _active_org_id():
    """The current tenant's organization_id (g first, then the user)."""
    org_id = getattr(g, "current_org_id", None)
    if org_id is None:
        org_id = getattr(current_user, "organization_id", None)
    return org_id


def _planned_work(org_id):
    """Each capability's planned work, read through the one slice registry: ``{capability id: [{name, href}]}``."""
    from app.modules.genome.slices.registry import get_slice

    planned = {}
    for node in get_slice("implementation", org_id)["nodes"]:
        if node["kind"] == "wp" and node.get("capability"):
            planned.setdefault(int(node["capability"].rsplit(":", 1)[1]), []).append(
                {"name": node["name"], "href": node.get("link")})
    return planned


def _capability_links(slice_dict):
    """Each capability's element page, so its name in the matrix opens it."""
    from app.modules.architecture.routes.archimate_crud.routes import _element_href

    return {
        c["id"]: _element_href("strategy", "Capability", c["archimate_element_id"])
        for c in slice_dict["capabilities"]
        if c.get("archimate_element_id")
    }


@genome_coverage_bp.route("/", methods=["GET"])
@login_required
def index():
    """Landing page with the 'Generate coverage matrix' button (no matrix yet)."""
    return render_template(
        "genome/coverage.html",
        matrix_html=None,
        error=None,
        org_id=_active_org_id(),
        summary=None,
    )


@genome_coverage_bp.route("/generate", methods=["GET", "POST"])
@login_required
def generate():
    """Build the slice for the current tenant and render the heatmap inline."""
    org_id = _active_org_id()
    matrix_html = None
    error = None
    summary = None
    if org_id is None:
        error = "No active organization for the current user."
    else:
        try:
            slice_dict = build_coverage_slice(org_id)
            # The deterministic emitter escapes every database-derived text field.
            matrix_html = Markup(  # nosec B704
                emit_coverage_matrix_html(slice_dict, _planned_work(org_id), _capability_links(slice_dict))
            )
            summary = support_summary(org_id)
            summary["generated_at"] = format_generated_at(summary["generated_at"])
            summary["supports"] = len(slice_dict["cells"])
        except SliceProvenanceError as exc:
            # Provenance is structural and non-optional — surface the failure,
            # never render a fabricated or half-provenanced matrix.
            logger.warning("Coverage slice provenance error for org %s: %s", org_id, exc)
            error = f"Coverage matrix could not be built: {exc}"
    return render_template(
        "genome/coverage.html",
        matrix_html=matrix_html,
        error=error,
        org_id=org_id,
        summary=summary,
    )


def register(app):
    """Register the Capability Coverage blueprint on the app."""
    app.register_blueprint(genome_coverage_bp)
    app.logger.info(
        "[BLUEPRINT] Capability Coverage registered at /genome/coverage"
    )
