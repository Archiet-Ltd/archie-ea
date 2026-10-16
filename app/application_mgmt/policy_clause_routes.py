"""Routes for policies with clauses bound to compliance controls, the compliance check, and
the privacy-assessment status of a processing activity.

Every route works on the organisation the user is working in (``current_org_id()``); a policy
or element of another organisation answers 404. Writes need one of the compliance write roles,
are CSRF-protected app-wide and audit-logged.
"""

from flask import jsonify, request
from flask_login import current_user, login_required

from app import db
from app.application_mgmt import application_mgmt
from app.decorators import audit_log, require_roles
from app.middleware.tenant_context import current_org_id
from app.modules.compliance.roles import COMPLIANCE_WRITE_ROLES
from app.modules.compliance.services.applicability_service import ApplicabilityService
from app.modules.solutions_strategic.v2.services.policy_monitoring_service import (
    PolicyMonitoringService,
    PolicyNotFound,
    PolicyRefused,
)

POLICIES_PATH = "/api/compliance/policies"


def _error(exc, status):
    db.session.rollback()
    return jsonify({"error": str(exc) or "Not found"}), status


@application_mgmt.route(POLICIES_PATH, methods=["POST"])
@login_required
@require_roles(*COMPLIANCE_WRITE_ROLES)
@audit_log("compliance_policy_create")
def create_compliance_policy():
    """Create a draft policy. It becomes active only when published."""
    data = request.get_json(silent=True) or {}
    if not (data.get("name") or "").strip():
        return jsonify({"error": "A policy needs a name"}), 400
    result = PolicyMonitoringService.create_policy(
        {
            "name": data["name"].strip(),
            "description": data.get("description"),
            "category": data.get("category") or "governance",
            "severity": data.get("severity") if data.get("severity") in ("critical", "high", "medium", "low") else "medium",
            "owner_id": current_user.id,
            "is_active": False,
        },
        organization_id=current_org_id(),
    )
    if not result.get("success"):
        db.session.rollback()
        return jsonify({"error": "The policy could not be created"}), 400
    return jsonify(result), 201


@application_mgmt.route(POLICIES_PATH + "/<int:policy_id>", methods=["GET"])
@login_required
def get_compliance_policy(policy_id):
    """A policy's clauses and the applications each one governs."""
    try:
        policy = PolicyMonitoringService._own_policy(current_org_id(), policy_id)
        return jsonify({
            "policy": policy.to_dict(),
            "clauses": PolicyMonitoringService.governed_applications(current_org_id(), policy_id),
        })
    except PolicyNotFound as exc:
        return _error(exc, 404)


@application_mgmt.route(POLICIES_PATH + "/<int:policy_id>/clauses", methods=["POST"])
@login_required
@require_roles(*COMPLIANCE_WRITE_ROLES)
@audit_log("compliance_policy_bind_clause")
def bind_policy_clause(policy_id):
    data = request.get_json(silent=True) or {}
    try:
        control_id = int(data.get("control_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Choose the control the clause is bound to"}), 400
    try:
        binding = PolicyMonitoringService.bind_clause(
            current_org_id(), policy_id, data.get("clause_ref"), data.get("clause_text"),
            control_id, current_user.id,
        )
        db.session.commit()
        return jsonify({"success": True, "clause": binding.to_dict()}), 201
    except PolicyNotFound as exc:
        return _error(exc, 404)
    except PolicyRefused as exc:
        return _error(exc, 409)


@application_mgmt.route(POLICIES_PATH + "/<int:policy_id>/clauses/<int:binding_id>", methods=["DELETE"])
@login_required
@require_roles(*COMPLIANCE_WRITE_ROLES)
@audit_log("compliance_policy_unbind_clause")
def unbind_policy_clause(policy_id, binding_id):
    try:
        PolicyMonitoringService.unbind_clause(current_org_id(), policy_id, binding_id)
        db.session.commit()
        return jsonify({"success": True})
    except PolicyNotFound as exc:
        return _error(exc, 404)
    except PolicyRefused as exc:
        return _error(exc, 409)


@application_mgmt.route(POLICIES_PATH + "/<int:policy_id>/publish", methods=["POST"])
@login_required
@require_roles(*COMPLIANCE_WRITE_ROLES)
@audit_log("compliance_policy_publish")
def publish_compliance_policy(policy_id):
    try:
        result = PolicyMonitoringService.publish_policy(current_org_id(), policy_id, current_user.id)
        db.session.commit()
        return jsonify({"success": True, **result})
    except PolicyNotFound as exc:
        return _error(exc, 404)
    except PolicyRefused as exc:
        return _error(exc, 409)


@application_mgmt.route(POLICIES_PATH + "/<int:policy_id>/versions", methods=["POST"])
@login_required
@require_roles(*COMPLIANCE_WRITE_ROLES)
@audit_log("compliance_policy_new_version")
def new_compliance_policy_version(policy_id):
    try:
        result = PolicyMonitoringService.new_policy_version(current_org_id(), policy_id, current_user.id)
        db.session.commit()
        return jsonify({"success": True, **result}), 201
    except PolicyNotFound as exc:
        return _error(exc, 404)
    except PolicyRefused as exc:
        return _error(exc, 409)


@application_mgmt.route(POLICIES_PATH + "/check", methods=["POST"])
@login_required
@require_roles(*COMPLIANCE_WRITE_ROLES)
@audit_log("compliance_policy_check")
def check_compliance_policies():
    """"Check now": raise and close the violations of every published policy clause."""
    result = PolicyMonitoringService.detect_clause_violations(current_org_id())
    db.session.commit()
    return jsonify({"success": True, **result})


@application_mgmt.route("/api/compliance/privacy-assessments/<int:element_id>/status", methods=["POST"])
@login_required
@require_roles(*COMPLIANCE_WRITE_ROLES)
@audit_log("compliance_privacy_assessment_status")
def set_privacy_assessment_status(element_id):
    """Record where the impact assessment for a processing activity stands."""
    data = request.get_json(silent=True) or {}
    try:
        ApplicabilityService.set_privacy_assessment_status(current_org_id(), element_id, data.get("status"))
        db.session.commit()
        return jsonify({"success": True, "status": data.get("status")})
    except LookupError as exc:
        return _error(exc, 404)
    except ValueError as exc:
        return _error(exc, 400)
