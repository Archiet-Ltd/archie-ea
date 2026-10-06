"""Capability ownership (R1-B03 PR 2): the "capabilities with no owner" list
and the owner read/write API, writing through the one ownership record
(ApplicationOwner) via capability_ownership_service.
"""
from __future__ import annotations

from flask import g, jsonify, render_template, request
from flask_login import login_required

from app.decorators import audit_log, role_required
from app.models.application_owner import ApplicationOwner
from app.models.user import ROLE_CTO, ROLE_PORTFOLIO_MANAGER
from app.services.capability_ownership_service import (
    CrossOrganisationCapabilityOwner,
    list_capabilities_with_no_owner,
    remove_capability_owner,
    set_capability_owner,
)

from . import capability_map


@capability_map.route("/capabilities/no-owner")
@login_required
@role_required(ROLE_CTO, ROLE_PORTFOLIO_MANAGER)
@audit_log("capabilities_no_owner_view")
def capabilities_no_owner():
    """Capabilities with no owner in the caller's organisation.

    Accessible by cto and portfolio_manager personas from their sidebar.
    """
    org_id = g.current_org_id
    capabilities = list_capabilities_with_no_owner(org_id)
    return render_template(
        "capabilities/no_owner.html",
        capabilities=capabilities,
    )


@capability_map.route("/api/capabilities/<int:capability_id>/owners", methods=["GET"])
@login_required
def api_capability_owners(capability_id):
    org_id = g.current_org_id
    rows = ApplicationOwner.get_display_rows_for_element("capability", capability_id, org_id)
    return jsonify({"owners": rows})


@capability_map.route("/api/capabilities/<int:capability_id>/owners", methods=["POST"])
@login_required
def api_set_capability_owner(capability_id):
    org_id = g.current_org_id
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    ownership_type = data.get("ownership_type", "primary")
    if not user_id:
        return jsonify({"message": "user_id is required"}), 400
    try:
        record = set_capability_owner(
            capability_id=capability_id,
            user_id=user_id,
            organization_id=org_id,
            ownership_type=ownership_type,
            assigned_by=getattr(g, "current_user_id", None),
        )
    except CrossOrganisationCapabilityOwner as exc:
        return jsonify({"message": str(exc)}), 400
    return jsonify({"owner": record.to_dict()}), 201


@capability_map.route("/api/capabilities/<int:capability_id>/owners/<int:owner_id>", methods=["DELETE"])
@login_required
def api_delete_capability_owner(capability_id, owner_id):
    org_id = g.current_org_id
    removed = remove_capability_owner(owner_record_id=owner_id, organization_id=org_id)
    if not removed:
        return jsonify({"message": "Owner record not found"}), 404
    return "", 204
