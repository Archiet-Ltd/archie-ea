"""Routes for the Business Case module.

Blueprint: business_case_bp, url_prefix="/business-case".
Index endpoint (linked from the sidebar by the orchestrator post-merge):
    business_case.index
"""

import logging

from werkzeug.exceptions import HTTPException
from flask import Blueprint, Response, redirect, render_template, request, url_for
from flask_login import current_user, login_required

# Destructive and mutating routes were guarded by @login_required only, so any
# authenticated user could delete another user's records. Matches the gating
# already used by app/modules/capabilities/routes/enterprise_crud_routes.py.
from app.decorators import require_roles
from app import db

from app.config.archimate_viewpoints import CANVAS_TEMPLATES
from app.models.business_case import BUSINESS_CASE_STATUSES
from app.models.business_capabilities import BusinessCapability
from app.models.solution_models import Solution
from app.models.risk import Risk
from app.models.strategic import StrategicInitiative
from app.models.unified_work_package import UnifiedWorkPackage
from app.services import work_package_service
from app.utils.api_response import error_response, not_found_response, success_response

from . import document, service

logger = logging.getLogger(__name__)

business_case_bp = Blueprint("business_case", __name__, url_prefix="/business-case")

# Who writes a case. The portfolio manager owns the cost and change story.
WRITE_ROLES = ("admin", "architect", "business_architect", "portfolio_manager")


def _link_options():
    """Simple select-list options for the capability/initiative/solution link pickers."""
    capabilities = BusinessCapability.query.order_by(BusinessCapability.name).all()
    initiatives = StrategicInitiative.query.order_by(StrategicInitiative.name).all()
    solutions = Solution.query.order_by(Solution.name).all()
    return capabilities, initiatives, solutions


def _work_packages():
    return work_package_service.query_for(current_user.organization_id).order_by(
        UnifiedWorkPackage.name).all()


@business_case_bp.route("/")
@login_required
def index():
    """List all Business Cases for the current tenant, with work package, cost, payback and decision."""
    business_cases = service.list_business_cases()
    return render_template(
        "business_case/index.html",
        business_cases=business_cases,
        documents={bc.id: document.build_document(bc) for bc in business_cases},
        work_packages=_work_packages(),
        can_edit=_can_edit(),
        statuses=BUSINESS_CASE_STATUSES,
    )


@business_case_bp.route("/create", methods=["POST"])
@login_required
@require_roles(*WRITE_ROLES)
def create():
    """Create a new business case and redirect to its document view."""
    title = (request.form.get("title") or "").strip()
    description = (request.form.get("description") or "").strip() or None

    try:
        business_case = service.create_business_case(
            title=title or "Untitled Business Case",
            description=description,
            created_by_id=getattr(current_user, "id", None),
            work_package_id=request.form.get("work_package_id") or None,
        )
    except ValueError as exc:
        logger.warning("Invalid business case create payload: %s", exc)
        return redirect(url_for("business_case.index"))

    return redirect(url_for("business_case.detail", business_case_id=business_case.id))


def _can_edit():
    """Whether the signed-in user may write a case: the one role helper, not a second rule."""
    try:
        require_roles(*WRITE_ROLES)(lambda: True)()
    except HTTPException:
        return False
    return True


@business_case_bp.route("/<int:business_case_id>")
@login_required
def detail(business_case_id):
    """The business case as a document: recommendation, problem, options, costs, risks, decision."""
    business_case = service.get_business_case_or_none(business_case_id)
    if business_case is None:
        return render_template("business_case/not_found.html", business_case_id=business_case_id), 404
    return render_template(
        "business_case/document.html",
        doc=document.build_document(business_case),
        business_case=business_case,
        can_edit=_can_edit(),
    )


@business_case_bp.route("/<int:business_case_id>/edit", methods=["GET", "POST"])
@login_required
@require_roles(*WRITE_ROLES)
def edit(business_case_id):
    """Edit the document: work package, problem, options and their figures, risks, decision."""
    business_case = service.get_business_case_or_none(business_case_id)
    if business_case is None:
        return render_template("business_case/not_found.html", business_case_id=business_case_id), 404
    error = None
    if request.method == "POST":
        form = request.form
        try:
            rows = []
            for i in range(document.MAX_OPTIONS):
                rows.append({
                    "name": form.get(f"option_{i}_name"),
                    "summary": form.get(f"option_{i}_summary"),
                    "advantages": form.get(f"option_{i}_advantages"),
                    "drawbacks": form.get(f"option_{i}_drawbacks"),
                    document.ONE_OFF: form.get(f"option_{i}_{document.ONE_OFF}"),
                    document.RUN_ANNUAL: form.get(f"option_{i}_{document.RUN_ANNUAL}"),
                    document.BENEFIT_ANNUAL: form.get(f"option_{i}_{document.BENEFIT_ANNUAL}"),
                    "recommended": form.get("recommended") == str(i),
                })
            service.update_business_case(
                business_case,
                title=(form.get("title") or "").strip() or business_case.title,
                work_package_id=form.get("work_package_id") or None,
                problem_statement=form.get("problem_statement"),
                status=form.get("status") or business_case.status,
                decision_note=form.get("decision_note"),
            )
            service.save_options(business_case, rows)
            service.save_risks(business_case, form.getlist("risk_ids"))
            return redirect(url_for("business_case.detail", business_case_id=business_case.id))
        except ValueError as exc:
            db.session.rollback()
            error = str(exc)
    doc = document.build_document(business_case)
    return render_template(
        "business_case/edit.html",
        doc=doc,
        business_case=business_case,
        work_packages=_work_packages(),
        risks=Risk.query.filter_by(organization_id=business_case.organization_id).order_by(Risk.title).all(),
        linked_risk_ids={r.id for r in doc.risks},
        statuses=BUSINESS_CASE_STATUSES,
        max_options=document.MAX_OPTIONS,
        error=error,
    ), (400 if error else 200)


@business_case_bp.route("/<int:business_case_id>/canvas")
@login_required
def canvas(business_case_id):
    """The canvas boards for this case (the sections the document reads from)."""
    business_case = service.get_business_case_or_none(business_case_id)
    if business_case is None:
        return render_template("business_case/not_found.html", business_case_id=business_case_id), 404

    capabilities, initiatives, solutions = _link_options()
    # Zones in record order, keyed by box_key.
    canvas_zones = CANVAS_TEMPLATES["business_case"]["zones"]

    from app.services.composer_export_formats import resolve_canvas_saved_diagram_id

    saved_diagram_id = resolve_canvas_saved_diagram_id(business_case, "business_case")

    return render_template(
        "business_case/detail.html",
        business_case=business_case,
        statuses=BUSINESS_CASE_STATUSES,
        capabilities=capabilities,
        initiatives=initiatives,
        solutions=solutions,
        canvas_zones={z["box_key"]: z for z in canvas_zones},
        canvas_unclassified_count=0,
        canvas_saved_diagram_id=saved_diagram_id,
    )


@business_case_bp.route("/<int:business_case_id>/export", methods=["GET"])
@login_required
def export_canvas(business_case_id):
    """Export this business case through the existing saved-viewpoint formats
    (mermaid, lucid, archi) over the record's saved diagram, with every empty
    box's reason named in the file.

    Query parameter: format (mermaid|lucid|archi, default mermaid).
    """
    business_case = service.get_business_case_or_none(business_case_id)
    if business_case is None:
        return not_found_response("Business Case")

    fmt = request.args.get("format", "mermaid")
    if fmt not in ("mermaid", "lucid", "archi"):
        return error_response(f"Unsupported format: {fmt}", code="VALIDATION_ERROR", status_code=400)

    from app.services.composer_export_formats import (
        export_canvas_viewpoint,
        resolve_canvas_saved_diagram_id,
    )

    saved_diagram_id = resolve_canvas_saved_diagram_id(business_case, "business_case")
    body, mimetype, ext = export_canvas_viewpoint(
        "business_case", saved_diagram_id, fmt,
        name=business_case.title or "Business Case",
    )
    filename = f"business-case-{business_case_id}.{ext}"
    return Response(
        body, status=200, mimetype=mimetype,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@business_case_bp.route("/<int:business_case_id>/update", methods=["POST"])
@login_required
@require_roles(*WRITE_ROLES)
def update(business_case_id):
    """Update business case meta fields (title/description/status/links) — JSON API."""
    business_case = service.get_business_case_or_none(business_case_id)
    if business_case is None:
        return not_found_response("Business Case")

    payload = request.get_json(silent=True) or request.form
    fields = {}
    for key in (
        "title",
        "description",
        "status",
        "capability_id",
        "strategic_initiative_id",
        "solution_id",
    ):
        if key in payload:
            fields[key] = payload.get(key)

    try:
        business_case = service.update_business_case(business_case, **fields)
    except ValueError as exc:
        return error_response(str(exc), code="VALIDATION_ERROR", status_code=400)

    return success_response(business_case.to_dict())


@business_case_bp.route("/<int:business_case_id>/delete", methods=["POST"])
@login_required
# DEF-075, Capgemini dry-run: this required "admin" while create/update/
# save_field above accept admin/architect/business_architect — so the
# business_architect who created a business case got a 403 (a full-page
# error, not an inline one, since this route redirects rather than
# returning JSON) trying to delete their own record. Same roles as
# create/update.
@require_roles(*WRITE_ROLES)
def delete(business_case_id):
    """Delete a business case."""
    business_case = service.get_business_case_or_none(business_case_id)
    if business_case is None:
        return not_found_response("Business Case")

    service.delete_business_case(business_case)
    return redirect(url_for("business_case.index"))


@business_case_bp.route("/<int:business_case_id>/api/field", methods=["POST", "PUT"])
@login_required
@require_roles(*WRITE_ROLES)
def save_field(business_case_id):
    """Save a single business-case document section (inline save).

    Payload: {"field": "problem_statement", "value": "..."}
    """
    business_case = service.get_business_case_or_none(business_case_id)
    if business_case is None:
        return not_found_response("Business Case")

    payload = request.get_json(silent=True) or {}
    field_name = payload.get("field")
    value = payload.get("value")

    if not field_name:
        return error_response("Missing 'field' field", code="VALIDATION_ERROR", status_code=400)

    try:
        business_case = service.save_field(business_case, field_name, value)
    except ValueError as exc:
        return error_response(str(exc), code="VALIDATION_ERROR", status_code=400)

    return success_response(business_case.to_dict())


@business_case_bp.route("/<int:business_case_id>/pull-financials", methods=["POST"])
@login_required
@require_roles(*WRITE_ROLES)
def pull_financials(business_case_id):
    """Re-run aggregate_financials() against the linked capability / initiative
    / solution and pre-populate any blank financial fields (JSON API)."""
    business_case = service.get_business_case_or_none(business_case_id)
    if business_case is None:
        return not_found_response("Business Case")

    report = service.refresh_financials_from_links(business_case)
    return success_response({"business_case": business_case.to_dict(), "aggregation": report})


@business_case_bp.route("/api/list")
@login_required
def api_list():
    """JSON list of all Business Cases for the current tenant."""
    cases = service.list_business_cases()
    return success_response([
        {
            "id": bc.id,
            "title": bc.title or "",
            "status": bc.status,
            "updated_at": bc.updated_at.isoformat() if bc.updated_at else None,
        }
        for bc in cases
    ])


@business_case_bp.route("/api/<int:business_case_id>")
@login_required
def api_detail(business_case_id):
    """JSON detail of one Business Case."""
    case = service.get_business_case_or_none(business_case_id)
    if case is None:
        return not_found_response("Business Case")
    return success_response(case.to_dict())


# Import AI section-draft route — adds POST /api/<id>/ai-draft-section to this
# blueprint (side-effect import), matching the
# app/modules/architecture/routes/arb_review_ai_routes.py pattern.
from app.modules.business_case import ai_routes  # noqa: F401, E402  # dead-code-ok
