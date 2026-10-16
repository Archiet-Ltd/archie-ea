"""API routes for compliance framework management."""

from datetime import datetime

from flask import current_app, jsonify, render_template, request
from flask_login import current_user, login_required

from app import db
from app.application_mgmt import application_mgmt
from app.decorators import audit_log, require_roles
from app.middleware.tenant_context import current_org_id
from app.models.application_compliance import ApplicationComplianceControl
from app.models.compliance_models import ComplianceControl, RegulatoryFramework
from app.modules.compliance.services.control_assurance_service import (
    ControlNotFound,
    list_control_tests,
    record_control_test,
    update_control_evidence,
)
from app.modules.compliance.services.evidence_gap_service import evidence_gaps
from app.utils.route_guards import require_entity


CONTROL_WORK_ROLES = (
    "admin",
    "architect",
    "business_architect",
    "security_architect",
    "compliance",
    "compliance_officer",
)


def _can_record_control_work() -> bool:
    """Whether to offer the record-a-test and assign-an-owner actions: the persona side of
    what the write routes below accept. The routes decide; this only keeps the page from
    offering actions that will be refused."""
    from app.utils.role_access import get_user_role, is_admin

    role = get_user_role(current_user)
    return is_admin(current_user) or role in CONTROL_WORK_ROLES or role.endswith("_architect")


@application_mgmt.route("/compliance", methods=["GET"])
@login_required
def compliance_frameworks_dashboard():
    """Read-only regulatory-compliance dashboard (G6).

    Surfaces the built-but-previously-orphaned RegulatoryFramework /
    ComplianceControl model — until now reachable only through the JSON API in
    this module, with no door in any persona's navigation. It is the
    security_architect's compliance surface (see role_access.py).

    RegulatoryFramework and ComplianceControl are GLOBAL master data (no
    organization_id); the app<->control mappings are org-scoped via
    ApplicationComplianceControl (TenantMixin), so its counts filter to the
    caller's organisation mechanically inside a request context. Every number
    below is a real query result — an empty estate renders zeros/— rather than a
    fabricated figure.
    """
    frameworks = (
        RegulatoryFramework.query.filter_by(status="active")
        .order_by(RegulatoryFramework.code)
        .all()
    )

    framework_rows = [
        {
            "id": f.id,
            "code": f.code,
            "name": f.name,
            "category": f.category,
            "jurisdiction": f.jurisdiction,
            "enforcement_level": f.enforcement_level,
            "penalty_risk": f.penalty_risk,
            "control_count": f.controls.count(),
        }
        for f in frameworks
    ]

    total_controls = ComplianceControl.query.filter_by(is_active=True).count()

    # Org-scoped mapping coverage (TenantMixin auto-filters inside the request).
    mappings = ApplicationComplianceControl.query.all()
    mapped_by_status = {}
    for m in mappings:
        status = m.implementation_status or "planned"
        mapped_by_status[status] = mapped_by_status.get(status, 0) + 1

    gaps = None
    gaps_error = False
    try:
        gaps = evidence_gaps(current_org_id())
    except Exception:  # noqa: BLE001 - the page still renders; the section says it could not load
        current_app.logger.exception("Could not build the evidence gap list")
        db.session.rollback()
        gaps_error = True

    return render_template(
        "compliance/frameworks_dashboard.html",
        evidence_gaps=gaps,
        evidence_gaps_error=gaps_error,
        can_record_control_work=_can_record_control_work(),
        frameworks=framework_rows,
        total_frameworks=len(framework_rows),
        total_controls=total_controls,
        total_mappings=len(mappings),
        mapped_by_status=mapped_by_status,
        load_error=False,
    )


@application_mgmt.route("/api/compliance/frameworks", methods=["GET"])
@login_required
def get_compliance_frameworks():
    """Get all active compliance frameworks."""
    frameworks = RegulatoryFramework.query.filter_by(status="active").all()
    return jsonify(
        [
            {
                "id": f.id,
                "code": f.code,
                "name": f.name,
                "description": f.description,
                "category": f.category,
                "enforcement_level": f.enforcement_level,
                "control_count": f.controls.count(),
            }
            for f in frameworks
        ]
    )


@application_mgmt.route("/api/compliance/frameworks/<int:framework_id>/controls", methods=["GET"])
@login_required
def get_framework_controls(framework_id):
    """Get all controls for a framework."""
    # An unknown framework must 404: an empty control list is otherwise
    # indistinguishable from a framework that has no controls.
    require_entity(RegulatoryFramework, framework_id, description="Framework not found")

    category = request.args.get("category")

    query = ComplianceControl.query.filter_by(framework_id=framework_id)

    if category:
        query = query.filter_by(category=category)

    controls = query.order_by(ComplianceControl.control_code).all()
    return jsonify(
        [
            {
                "id": c.id,
                "control_code": c.control_code,
                "title": c.title,
                "description": c.description,
                "category": c.category,
                "priority": c.priority,
            }
            for c in controls
        ]
    )


@application_mgmt.route("/api/applications/<string:app_id>/compliance", methods=["GET"])
@login_required
def get_application_compliance(app_id):
    """Get compliance control mappings for an application."""
    mappings = ApplicationComplianceControl.query.filter_by(application_id=app_id).all()

    result = {
        "application_id": app_id,
        "mappings": [m.to_dict() for m in mappings],
        "summary": {
            "total_controls": len(mappings),
            "implemented": len([m for m in mappings if m.status == "implemented"]),
            "planned": len([m for m in mappings if m.status == "planned"]),
            "not_implemented": len([m for m in mappings if m.status == "not_implemented"]),
        },
    }

    return jsonify(result)


@application_mgmt.route("/api/applications/<string:app_id>/compliance/map", methods=["POST"])
@login_required
@require_roles("admin", "architect", "business_architect")
def map_compliance_control(app_id):
    """Map a compliance control to an application component."""
    data = request.get_json()

    if not data or "control_id" not in data:
        return jsonify({"error": "control_id required"}), 400

    # Check if mapping already exists
    existing = ApplicationComplianceControl.query.filter_by(
        control_id=data["control_id"], application_id=app_id
    ).first()

    if existing:
        return jsonify({"error": "Mapping already exists"}), 409

    try:
        mapping = ApplicationComplianceControl(
            application_id=app_id,
            control_id=data["control_id"],
            status=data.get("status", "not_implemented"),
            coverage_percentage=data.get("coverage_percentage", 0),
            implementation_notes=data.get("implementation_notes"),
            evidence=data.get("evidence"),
        )

        db.session.add(mapping)
        db.session.commit()

        return jsonify({"success": True, "mapping": mapping.to_dict()})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating compliance mapping: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/compliance/mappings/<int:mapping_id>", methods=["PUT"])
@login_required
@require_roles(*CONTROL_WORK_ROLES)
@audit_log("compliance_mapping_update")
def update_compliance_mapping(mapping_id):
    """Update an adopted control: its status, notes and its evidence (link, validity, owner).

    Writes the columns ``ApplicationComplianceControl`` really has. The control row must
    belong to the caller's organisation; another organisation's row answers 404.
    """
    data = request.get_json(silent=True) or {}
    changes = {}
    try:
        if "implementation_status" in data:
            changes["implementation_status"] = str(data["implementation_status"])[:20]
        if "notes" in data:
            changes["notes"] = data["notes"]
        if "evidence_url" in data:
            changes["evidence_url"] = data["evidence_url"]
        for field in ("evidence_valid_until", "evidence_collected_at"):
            if field in data:
                raw = data[field]
                changes[field] = datetime.fromisoformat(raw) if raw else None
        if "evidence_owner_id" in data:
            changes["evidence_owner_id"] = data["evidence_owner_id"]
    except (TypeError, ValueError):
        return jsonify({"error": "A date must be written as YYYY-MM-DD"}), 400

    try:
        mapping = update_control_evidence(current_org_id(), mapping_id, **changes)
        db.session.commit()
        return jsonify({"success": True, "mapping": mapping.to_dict()})
    except ControlNotFound:
        db.session.rollback()
        return jsonify({"error": "Control not found"}), 404
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(
            "Failed to update compliance mapping mapping_id=%s user=%s: %s",
            mapping_id,
            getattr(current_user, "id", None),
            e,
            exc_info=True,
        )
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route("/api/compliance/controls/<int:control_row_id>/tests", methods=["GET"])
@login_required
def list_control_tests_route(control_row_id):
    """The tests recorded on one adopted control, newest first, and its effectiveness."""
    try:
        return jsonify(list_control_tests(current_org_id(), control_row_id))
    except ControlNotFound:
        return jsonify({"error": "Control not found"}), 404


@application_mgmt.route("/api/compliance/controls/<int:control_row_id>/tests", methods=["POST"])
@login_required
@require_roles(*CONTROL_WORK_ROLES)
@audit_log("control_test_create")
def record_control_test_route(control_row_id):
    """Record a design or operating test on an adopted control."""
    data = request.get_json(silent=True) or {}
    try:
        test = record_control_test(
            current_org_id(),
            control_row_id,
            test_kind=data.get("test_kind"),
            result=data.get("result"),
            tested_by_id=current_user.id,
            notes=data.get("notes"),
            evidence_url=data.get("evidence_url"),
        )
        db.session.commit()
        return jsonify({"success": True, "test": test.to_dict()}), 201
    except ControlNotFound:
        db.session.rollback()
        return jsonify({"error": "Control not found"}), 404
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400


@application_mgmt.route("/api/compliance/mappings/<int:mapping_id>", methods=["DELETE"])
@login_required
@require_roles("admin", "architect", "business_architect")
def delete_compliance_mapping(mapping_id):
    """Remove a compliance mapping."""
    mapping = ApplicationComplianceControl.query.get_or_404(mapping_id)

    try:
        db.session.delete(mapping)
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(
            "Failed to delete compliance mapping mapping_id=%s user=%s: %s",
            mapping_id,
            getattr(current_user, "id", None),
            e,
            exc_info=True,
        )
        return jsonify({"error": "An internal error occurred"}), 500
