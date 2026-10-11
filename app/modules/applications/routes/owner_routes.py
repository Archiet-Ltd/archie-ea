"""Owner writer routes for the Applications module.

Provides:
- Add an owner (POST, JSON)
- Change an owner's type (PUT, JSON)
- Remove an owner (DELETE, JSON)

All routes are scoped to the caller's organisation and refuse cross-org
assignment. The owner text columns on ApplicationComponent are read-only
in the edit form — ownership is recorded only through this module.
"""

from __future__ import annotations

import logging

from flask import g, jsonify, request
from flask_login import current_user, login_required

from app import db
from app.decorators import audit_log
from app.models.application_owner import ApplicationOwner
from app.utils.tenant_users import user_in_org

from . import unified_applications_bp
from ._helpers import _verify_app_in_org

logger = logging.getLogger(__name__)
def _duplicate_owner(app_id: int, user_id: int, ownership_type: str, org_id: int, exclude_owner_id: int | None = None):
    """Return an existing owner row with the same user and type, if any."""
    query = ApplicationOwner.query.filter(
        ApplicationOwner.application_id == app_id,
        ApplicationOwner.user_id == user_id,
        ApplicationOwner.ownership_type == ownership_type,
        ApplicationOwner.organization_id == org_id,
    )
    if exclude_owner_id is not None:
        query = query.filter(ApplicationOwner.id != exclude_owner_id)
    return query.first()


@unified_applications_bp.route("/<int:app_id>/owners", methods=["POST"])
@login_required
@audit_log("application_owner_add")
def add_owner(app_id: int):
    """Add an owner to an application (JSON only).

    Body: {"user_id": int, "ownership_type": "primary"|"backup"|"technical"|"business"}
    Refuses assignment of a user from another organisation.  Also refuses
    writing to an application that does not belong to the caller.
    """
    org_id = g.current_org_id

    # Verify the application belongs to the caller's organisation
    app = _verify_app_in_org(app_id, org_id)
    if app is None:
        return jsonify({"success": False, "error": "Application not found"}), 404

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "error": "Request body must be JSON"}), 400

    user_id = data.get("user_id")
    ownership_type = (data.get("ownership_type") or "primary").strip().lower()

    if ownership_type not in ApplicationOwner.OWNERSHIP_TYPES:
        return jsonify({
            "success": False,
            "error": f"ownership_type must be one of {ApplicationOwner.OWNERSHIP_TYPES}",
        }), 400

    user = user_in_org(user_id, org_id)
    if user is None:
        return jsonify({
            "success": False,
            "error": "User not found in your organisation",
        }), 404

    # Check for duplicate (same user + same type)
    existing = _duplicate_owner(app_id, user.id, ownership_type, org_id)
    if existing is not None:
        return jsonify({
            "success": False,
            "error": f"User already assigned as {ownership_type} owner",
        }), 409

    owner = ApplicationOwner(
        application_id=app_id,
        user_id=user.id,
        assigned_by=current_user.id,
        organization_id=org_id,
        ownership_type=ownership_type,
    )
    db.session.add(owner)
    db.session.commit()

    return jsonify({
        "success": True,
        "owner": owner.to_dict(),
    }), 201


@unified_applications_bp.route("/<int:app_id>/owners/<int:owner_id>", methods=["PUT"])
@login_required
@audit_log("application_owner_change_type")
def change_owner_type(app_id: int, owner_id: int):
    """Change an owner's type (JSON only)."""
    org_id = g.current_org_id

    # Verify the application belongs to the caller's organisation
    app = _verify_app_in_org(app_id, org_id)
    if app is None:
        return jsonify({"success": False, "error": "Application not found"}), 404

    owner = ApplicationOwner.query.filter(
        ApplicationOwner.id == owner_id,
        ApplicationOwner.application_id == app_id,
        ApplicationOwner.organization_id == org_id,
    ).first()
    if owner is None:
        return jsonify({"success": False, "error": "Owner record not found"}), 404

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "error": "Request body must be JSON"}), 400

    new_type = (data.get("ownership_type") or "").strip().lower()
    if new_type not in ApplicationOwner.OWNERSHIP_TYPES:
        return jsonify({
            "success": False,
            "error": f"ownership_type must be one of {ApplicationOwner.OWNERSHIP_TYPES}",
        }), 400

    existing = _duplicate_owner(app_id, owner.user_id, new_type, org_id, exclude_owner_id=owner.id)
    if existing is not None:
        return jsonify({
            "success": False,
            "error": f"User already assigned as {new_type} owner",
        }), 409

    owner.ownership_type = new_type
    db.session.commit()

    return jsonify({
        "success": True,
        "owner": owner.to_dict(),
    })


@unified_applications_bp.route("/<int:app_id>/owners/<int:owner_id>", methods=["DELETE"])
@login_required
@audit_log("application_owner_remove")
def remove_owner(app_id: int, owner_id: int):
    """Remove an owner from an application."""
    org_id = g.current_org_id

    # Verify the application belongs to the caller's organisation
    app = _verify_app_in_org(app_id, org_id)
    if app is None:
        return jsonify({"success": False, "error": "Application not found"}), 404

    owner = ApplicationOwner.query.filter(
        ApplicationOwner.id == owner_id,
        ApplicationOwner.application_id == app_id,
        ApplicationOwner.organization_id == org_id,
    ).first()
    if owner is None:
        return jsonify({"success": False, "error": "Owner record not found"}), 404

    db.session.delete(owner)
    db.session.commit()

    return jsonify({"success": True}), 200


@unified_applications_bp.route("/<int:app_id>/owners", methods=["GET"])
@login_required
def list_owners(app_id: int):
    """List all owners for an application, with user details."""
    # Verify the application exists and is in the caller's organisation
    app = _verify_app_in_org(app_id, g.current_org_id)
    if app is None:
        return jsonify({"success": False, "error": "Application not found"}), 404

    org_id = g.current_org_id
    return jsonify({"owners": ApplicationOwner.get_display_rows_for_application(app_id, org_id)})
