"""The one roadmap: plateaus and work packages as a board, on one page.

``/genome/roadmap/`` reads the one roadmap store (plateaus and work packages),
shapes it into the four roadmap columns and shows it through five lenses
(Strategic, Capability, Application, Technology, Product). A programme, solution
or application roadmap is this page filtered with ``?programme=``,
``?solution=`` or ``?application=``, never a page of its own. Every older
roadmap address redirects here with its lens and filter (``roadmap_redirects``).

Read only: creating and editing work packages stays on the work package pages
the cards link to.
"""
from __future__ import annotations

import logging

from flask import Blueprint, abort, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.modules.genome.services.roadmap_board import (
    DEFAULT_LENS,
    LENS_LABELS,
    LENSES,
    build_roadmap_board,
)
from app.modules.genome.services.roadmap_slice import SliceProvenanceError

logger = logging.getLogger(__name__)

genome_roadmap_bp = Blueprint(
    "genome_roadmap",
    __name__,
    url_prefix="/genome/roadmap",
    template_folder="../templates",
)


def _active_org_id():
    """The current tenant's organization_id (g first, then the user)."""
    org_id = getattr(g, "current_org_id", None)
    if org_id is None:
        org_id = getattr(current_user, "organization_id", None)
    return org_id


def _int_arg(name):
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        abort(404)


def _owned_name(model, record_id, org_id):
    """Name of a record in this organisation, or a 404 when it is not theirs."""
    row = db.session.query(model).filter(
        model.id == record_id, model.organization_id == org_id
    ).first()
    if row is None:
        abort(404)
    return row.name


def _lens_url(lens, **filters):
    args = {k: v for k, v in filters.items() if v is not None}
    if lens != DEFAULT_LENS:
        args["lens"] = lens
    return url_for("genome_roadmap.index", **args)


@genome_roadmap_bp.route("/", methods=["GET"])
@login_required
def index():
    """The roadmap board for the signed-in organisation, in one lens."""
    org_id = _active_org_id()
    lens = request.args.get("lens", DEFAULT_LENS)
    if lens not in LENSES:
        lens = DEFAULT_LENS
    programme_id = _int_arg("programme")
    solution_id = _int_arg("solution")
    application_id = _int_arg("application")

    # The levels, outermost first: Enterprise > Programme > Solution > Application.
    crumbs = [("Enterprise", _lens_url(lens) if (programme_id or solution_id or application_id) else None)]
    if org_id is not None:
        from app.models.application_portfolio import ApplicationComponent
        from app.models.solution_models import Solution
        from app.models.vendor.vendor_organization import EnterpriseInitiative

        if programme_id is not None:
            crumbs.append((
                _owned_name(EnterpriseInitiative, programme_id, org_id),
                _lens_url(lens, programme=programme_id) if (solution_id or application_id) else None,
            ))
        if solution_id is not None:
            crumbs.append((
                _owned_name(Solution, solution_id, org_id),
                _lens_url(lens, programme=programme_id, solution=solution_id) if application_id else None,
            ))
        if application_id is not None:
            crumbs.append((_owned_name(ApplicationComponent, application_id, org_id), None))

    board = None
    error = None
    if org_id is None:
        error = "Your account is not attached to an organisation, so there is no roadmap to show."
    else:
        try:
            board = build_roadmap_board(
                org_id, lens, programme_id=programme_id,
                solution_id=solution_id, application_id=application_id,
            )
        except SliceProvenanceError as exc:
            # Provenance is structural: surface the failure, never draw a roadmap
            # that points at an element that is not there.
            logger.warning("Roadmap provenance error for org %s: %s", org_id, exc)
            error = "The roadmap could not be built: %s" % exc

    critical_paths = []
    if board is not None and lens == DEFAULT_LENS and not board["filtered"]:
        try:
            from app.services import work_package_service

            critical_paths = work_package_service.critical_paths_for_roadmap(org_id)
        except Exception as exc:  # the board above still shows
            logger.error("Roadmap critical path failed: %s", exc)
            db.session.rollback()

    filters = {"programme": programme_id, "solution": solution_id, "application": application_id}
    tabs = [
        (LENS_LABELS[name], _lens_url(name, **filters), name == lens) for name in LENSES
    ]
    return render_template(
        "genome/roadmap.html",
        board=board,
        plateaus=board["plateaus"] if board else [],
        error=error,
        lens=lens,
        tabs=tabs,
        crumbs=crumbs,
        critical_paths=critical_paths,
        clear_url=url_for("genome_roadmap.index", lens=lens) if board and board["filtered"] else None,
    )


@genome_roadmap_bp.route("/generate", methods=["POST"])
@login_required
def generate():
    """Bookmarked button from the earlier page: the roadmap now builds itself."""
    return redirect(url_for("genome_roadmap.index"), code=302)


def register(app):
    """Register the Transformation Roadmap blueprint on the app."""
    app.register_blueprint(genome_roadmap_bp)
    app.logger.info(
        "[BLUEPRINT] Transformation Roadmap registered at /genome/roadmap"
    )
