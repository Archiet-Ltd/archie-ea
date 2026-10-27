"""Capability Coverage page — deterministic genome slice, org-scoped read.

One read route + one page. The presenter clicks "Generate coverage matrix";
the server builds the business-layer genome slice for the current tenant
(zero LLM), the deterministic Jinja emitter renders it to an HTML heatmap, and
the page shows it with element-level provenance on every populated cell.

No write path — pure read + deterministic render.
"""
from __future__ import annotations

import logging

from flask import Blueprint, g, render_template
from flask_login import current_user, login_required
from markupsafe import Markup

from app.modules.genome.emit.coverage_matrix import emit_coverage_matrix_html
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


@genome_coverage_bp.route("/", methods=["GET"])
@login_required
def index():
    """Landing page with the 'Generate coverage matrix' button (no matrix yet)."""
    return render_template(
        "genome/coverage.html",
        matrix_html=None,
        error=None,
        org_id=_active_org_id(),
    )


@genome_coverage_bp.route("/generate", methods=["GET", "POST"])
@login_required
def generate():
    """Build the slice for the current tenant and render the heatmap inline."""
    org_id = _active_org_id()
    matrix_html = None
    error = None
    if org_id is None:
        error = "No active organization for the current user."
    else:
        try:
            slice_dict = build_coverage_slice(org_id)
            # The deterministic emitter escapes every database-derived text field.
            matrix_html = Markup(  # nosec B704
                emit_coverage_matrix_html(slice_dict, _planned_work(org_id))
            )
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
    )


def register(app):
    """Register the Capability Coverage blueprint on the app."""
    app.register_blueprint(genome_coverage_bp)
    app.logger.info(
        "[BLUEPRINT] Capability Coverage registered at /genome/coverage"
    )
