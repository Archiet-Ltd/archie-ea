"""Routes for the Organization module: enterprise org chart + RACI matrix.

Blueprint: organization_bp, url_prefix="/organization".
Index endpoint (linked from the sidebar by the orchestrator post-merge):
    organization.index
"""

import logging

from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required

# Destructive and mutating routes were guarded by @login_required only, so any
# authenticated user could delete another user's records. Matches the gating
# already used by app/modules/capabilities/routes/enterprise_crud_routes.py.
from app.decorators import require_roles

from app import db
from app.models.organization_model import RACI_VALUES, STAKEHOLDER_TYPES
from app.utils.api_response import error_response, not_found_response, success_response

from . import service

logger = logging.getLogger(__name__)

organization_bp = Blueprint("organization", __name__, url_prefix="/organization")


@organization_bp.route("/")
@login_required
def index():
    """Landing page linking the org chart and the enterprise RACI matrix."""
    return render_template("organization/index.html")


# ---------------------------------------------------------------------------
# Org chart
# ---------------------------------------------------------------------------


@organization_bp.route("/chart")
@login_required
def chart():
    """Org chart page — D3 tree of BusinessActors linked by
    Composition/Aggregation relationships (or a flat fallback)."""
    return render_template("organization/chart.html")


@organization_bp.route("/chart/api/data")
@login_required
def chart_data():
    """JSON hierarchy data consumed by the D3 org chart."""
    tree = service.build_org_tree()
    return success_response(tree)


@organization_bp.route("/workforce-transition")
@login_required
def workforce_transition():
    """Read-only workforce-transition view (fetches the api route below)."""
    return render_template("organization/workforce_transition.html")


@organization_bp.route("/workforce-transition/api")
@login_required
def workforce_transition_api():
    """As-is → to-be workforce transition analysis for the current org — role-to-role
    transitions, retiring roles, headcount delta and the skills gap. Reads the
    BusinessRole workforce fields that were previously written but never surfaced."""
    from app.services.workforce_transition_service import WorkforceTransitionService
    return success_response(WorkforceTransitionService.analyze())


def _role_to_edit_dict(role):
    """The workforce-specific fields this form owns — deliberately a small
    subset of BusinessRole's real columns (job_family/job_level/salary/etc.
    stay reachable only through the generic ArchiMate element form, which is
    where they belong). See _apply_role_workforce_fields for why."""
    import json

    try:
        skills = json.loads(role.required_skills) if role.required_skills else []
    except (ValueError, TypeError):
        skills = []
    return {
        "id": role.id,
        "name": role.name,
        "current_filled_positions": role.current_filled_positions,
        "forecasted_demand": role.forecasted_demand,
        "replacement_role_id": role.replacement_role_id,
        "required_skills": skills,
        "deprecated": role.deprecated_date is not None,
    }


@organization_bp.route("/workforce-transition/api/roles")
@login_required
def workforce_transition_roles_api():
    """List BusinessRoles for the role picker (create-transition and
    replacement-role select). Excludes the row being edited, if any, from the
    'replacement' options — passed as ?exclude=<id> — a role can't replace
    itself."""
    from app.models.business_layer import BusinessRole

    exclude_id = request.args.get("exclude", type=int)
    q = BusinessRole.query.order_by(BusinessRole.name)
    if exclude_id:
        q = q.filter(BusinessRole.id != exclude_id)
    return success_response([{"id": r.id, "name": r.name} for r in q.all()])


@organization_bp.route("/workforce-transition/api/roles/<int:role_id>")
@login_required
def workforce_transition_role_detail_api(role_id):
    """One role's workforce fields, for the edit form."""
    from app.models.business_layer import BusinessRole

    role = BusinessRole.query.filter_by(id=role_id).first()
    if role is None:
        return not_found_response("Business role")
    return success_response(_role_to_edit_dict(role))


def _apply_role_workforce_fields(role, data):
    """Set-only, one field at a time, per key present — mirrors the
    _apply_optional_capability_fields pattern (enterprise_crud_routes.py):
    a field the caller didn't send is left untouched, not cleared, so a
    partial PATCH from this narrow form can never silently wipe a value set
    elsewhere. Returns a list of validation errors."""
    import json

    errors = []
    if "current_filled_positions" in data:
        raw = data.get("current_filled_positions")
        if raw in (None, ""):
            role.current_filled_positions = None
        else:
            try:
                v = int(raw)
            except (TypeError, ValueError):
                errors.append("Current filled positions must be a number")
            else:
                if v < 0:
                    errors.append("Current filled positions cannot be negative")
                else:
                    role.current_filled_positions = v

    if "forecasted_demand" in data:
        raw = data.get("forecasted_demand")
        if raw in (None, ""):
            role.forecasted_demand = None
        else:
            try:
                v = int(raw)
            except (TypeError, ValueError):
                errors.append("Forecasted demand must be a number")
            else:
                if v < 0:
                    errors.append("Forecasted demand cannot be negative")
                else:
                    role.forecasted_demand = v

    if "replacement_role_id" in data:
        raw = data.get("replacement_role_id")
        if raw in (None, "", 0, "0"):
            role.replacement_role_id = None
        else:
            try:
                replacement_id = int(raw)
            except (TypeError, ValueError):
                errors.append("Replacement role id must be a number")
            else:
                if role.id is not None and replacement_id == role.id:
                    errors.append("A role cannot replace itself")
                else:
                    from app.models.business_layer import BusinessRole

                    replacement = BusinessRole.query.filter_by(id=replacement_id).first()
                    if replacement is None:
                        errors.append("Replacement role not found")
                    else:
                        role.replacement_role_id = replacement.id

    if "required_skills" in data:
        raw = data.get("required_skills")
        if raw in (None, ""):
            role.required_skills = None
        elif isinstance(raw, list):
            cleaned = [str(s).strip() for s in raw if str(s).strip()]
            role.required_skills = json.dumps(cleaned) if cleaned else None
        else:
            errors.append("Required skills must be a list")

    if "deprecated" in data:
        from datetime import date as _date
        role.deprecated_date = _date.today() if data.get("deprecated") else None

    return errors


@organization_bp.route("/workforce-transition/api/roles", methods=["POST"])
@login_required
@require_roles("admin", "architect", "business_architect")
def workforce_transition_role_create():
    """Create a Business Role with its workforce-transition fields set from
    the start — the gap this endpoint closes: previously the only way to
    populate current_filled_positions/forecasted_demand/replacement_role_id/
    required_skills was direct DB/CLI access; a business user had no path at
    all (2 Sep 2026, Capgemini delivery-team dry-run)."""
    from app.models.business_layer import BusinessRole

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return error_response("A role name is required", code="VALIDATION_ERROR", status_code=400)

    role = BusinessRole(name=name[:255])
    errors = _apply_role_workforce_fields(role, data)
    if errors:
        return error_response("; ".join(errors), code="VALIDATION_ERROR", status_code=400)

    db.session.add(role)
    db.session.commit()
    return success_response(_role_to_edit_dict(role))


@organization_bp.route("/workforce-transition/api/roles/<int:role_id>", methods=["PATCH"])
@login_required
@require_roles("admin", "architect", "business_architect")
def workforce_transition_role_update(role_id):
    """Update an existing Business Role's workforce-transition fields."""
    from app.models.business_layer import BusinessRole

    role = BusinessRole.query.filter_by(id=role_id).first()
    if role is None:
        return not_found_response("Business role")

    data = request.get_json(silent=True) or {}
    if "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            return error_response("Role name cannot be blank", code="VALIDATION_ERROR", status_code=400)
        role.name = name[:255]

    errors = _apply_role_workforce_fields(role, data)
    if errors:
        db.session.rollback()
        return error_response("; ".join(errors), code="VALIDATION_ERROR", status_code=400)

    db.session.commit()
    return success_response(_role_to_edit_dict(role))


# ---------------------------------------------------------------------------
# Enterprise RACI matrix
# ---------------------------------------------------------------------------


@organization_bp.route("/raci")
@login_required
def raci():
    """RACI matrix page — capabilities × stakeholders grid."""
    return render_template("organization/raci.html")


@organization_bp.route("/raci/api/data")
@login_required
def raci_data():
    """JSON payload for the RACI matrix: capability columns + all saved
    assignments (which also implicitly enumerate the stakeholder rows)."""
    capabilities = [
        {"id": c.id, "name": c.name, "code": c.code, "level": c.level}
        for c in service.list_matrix_capabilities()
    ]
    assignments = [a.to_dict() for a in service.list_raci_assignments()]
    return success_response({"capabilities": capabilities, "assignments": assignments})


@organization_bp.route("/raci/api/cell", methods=["POST"])
@login_required
@require_roles("admin", "architect", "business_architect")
def raci_cell_save():
    """Upsert one RACI matrix cell.

    Payload: {
        "stakeholder_type": "actor" | "role" | "user",
        "stakeholder_id": int,
        "stakeholder_name": str,
        "capability_id": int,
        "raci": "R" | "A" | "C" | "I",
        "notes": str (optional)
    }
    """
    payload = request.get_json(silent=True) or {}
    stakeholder_type = payload.get("stakeholder_type")
    stakeholder_id = payload.get("stakeholder_id")
    stakeholder_name = payload.get("stakeholder_name")
    capability_id = payload.get("capability_id")
    raci_value = payload.get("raci")
    notes = payload.get("notes")

    try:
        assignment = service.upsert_raci_cell(
            stakeholder_type=stakeholder_type,
            stakeholder_id=stakeholder_id,
            stakeholder_name=stakeholder_name,
            capability_id=capability_id,
            raci=raci_value,
            notes=notes,
        )
    except (service.StakeholderNotInOrganisation, service.CapabilityNotInOrganisation) as exc:
        return error_response(str(exc), code="NOT_FOUND", status_code=404)
    except ValueError as exc:
        return error_response(str(exc), code="VALIDATION_ERROR", status_code=400)

    return success_response(assignment.to_dict())


@organization_bp.route("/raci/api/cell", methods=["DELETE"])
@login_required
@require_roles("admin", "architect", "business_architect")
def raci_cell_delete():
    """Clear one RACI matrix cell.

    Payload: {"stakeholder_type": ..., "stakeholder_id": ..., "capability_id": ...}
    """
    payload = request.get_json(silent=True) or {}
    stakeholder_type = payload.get("stakeholder_type")
    stakeholder_id = payload.get("stakeholder_id")
    capability_id = payload.get("capability_id")

    if stakeholder_type not in STAKEHOLDER_TYPES or not stakeholder_id or not capability_id:
        return error_response("Invalid cell reference", code="VALIDATION_ERROR", status_code=400)

    try:
        service.require_organisation_capability(capability_id)
    except service.CapabilityNotInOrganisation:
        return not_found_response("RACI assignment")
    deleted = service.delete_raci_cell(stakeholder_type, stakeholder_id, capability_id)
    if not deleted:
        return not_found_response("RACI assignment")

    return success_response({"deleted": True})


@organization_bp.route("/api/stakeholders/search")
@login_required
def stakeholder_search():
    """Live-search endpoint for the "add stakeholder" picker on the RACI
    matrix. Searches BusinessActor, BusinessRole and User by name/email."""
    q = request.args.get("q", "")
    results = service.search_stakeholders(q, limit=10)
    return jsonify({"results": results})


@organization_bp.route("/raci/api/stakeholder/keep", methods=["POST"])
@login_required
@require_roles("admin", "architect", "business_architect")
def raci_stakeholder_keep():
    """Save a stakeholder on the matrix before any cell is set, so a reload keeps them."""
    payload = request.get_json(silent=True) or {}
    try:
        assignment = service.keep_raci_stakeholder(
            stakeholder_type=payload.get("stakeholder_type"),
            stakeholder_id=payload.get("stakeholder_id"),
            stakeholder_name=payload.get("stakeholder_name"),
        )
    except service.StakeholderNotInOrganisation as exc:
        return error_response(str(exc), code="NOT_FOUND", status_code=404)
    except ValueError as exc:
        return error_response(str(exc), code="VALIDATION_ERROR", status_code=400)
    return success_response(assignment.to_dict())


@organization_bp.route("/raci/api/stakeholder", methods=["POST"])
@login_required
@require_roles("admin", "architect", "business_architect")
def raci_stakeholder_create():
    """Create a Business Actor by name from the RACI picker.

    The 2 Sep 2026 audit (F-08) found the Add Stakeholder modal was a search
    over existing actors/roles/users with no way to add one that did not exist
    yet — "no name field, so no stakeholder can be added". A new actor is a
    real ArchiMate Business Actor (its before_insert listener mirrors it into
    the backbone) and TenantMixin scopes it to the caller's organisation.
    Returns the same {type, id, name, sublabel} shape the search returns so the
    picker can add it directly."""
    from app import db
    from app.models.business_layer import BusinessActor

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return error_response("A name is required", code="VALIDATION_ERROR", status_code=400)
    actor_type = (data.get("actor_type") or "Individual").strip()
    try:
        actor = BusinessActor(name=name[:255], actor_type=actor_type)
    except ValueError as exc:  # invalid actor_type — the model's validator names the valid set
        return error_response(str(exc), code="VALIDATION_ERROR", status_code=400)
    db.session.add(actor)
    db.session.commit()
    return success_response({
        "type": "actor",
        "id": actor.id,
        "name": actor.name,
        "sublabel": actor.actor_type or "Actor",
    })


__all__ = ["organization_bp", "RACI_VALUES"]
