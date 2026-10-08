"""
User Role Management Routes

Allows platform admins to assign enterprise roles to users.
Part of North Star Persona MVP.
"""

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from flask_login import login_required

from app.decorators import admin_required
from app.extensions import db
from app.middleware.tenant_decorators import require_org_or_platform_admin
from app.models.user import ROLE_DISPLAY_NAMES, VALID_ROLES, User

# Use the existing admin blueprint - this will be imported by admin_routes
user_role_bp = Blueprint("user_role", __name__)


@user_role_bp.route("/user/<int:user_id>/role", methods=["GET"])
@login_required
@admin_required
def edit_user_role(user_id):
    """Edit a user's enterprise role."""
    # admin_required is org-scoped admin, not platform_admin — restrict to the
    # current org (tenant-scoping-ok: fixes cross-org role-escalation IDOR).
    user = User.query.filter_by(id=user_id, organization_id=g.current_org_id).first_or_404()
    roles = [(r, ROLE_DISPLAY_NAMES.get(r, r)) for r in VALID_ROLES]
    return render_template("admin/user_role_edit.html", user=user, roles=roles)


@user_role_bp.route("/user/<int:user_id>/role", methods=["POST"])
@login_required
@admin_required
def update_user_role(user_id):
    """Update a user's enterprise role."""
    # tenant-scoping-ok: admin_required only checks the caller's own,
    # organisation-independent Permission.ADMINISTER bit (an Administrator
    # in their own org is globally True), while the User.query filter below
    # correctly scopes the lookup to g.current_org_id. Without this guard, a
    # caller who is an Administrator in org A but holds only a Viewer
    # OrgRole in org B can switch the active session to org B and rewrite
    # org B's own member's enterprise role. Found by the sweep that found
    # change_user_email's identical gap in admin_routes.py (commit
    # 7ae1b168); same tenant_decorators.require_org_or_platform_admin guard
    # used by every other fixed route on this branch, applied here since
    # this is the view function that actually answers POST
    # /admin/user/<user_id>/role.
    require_org_or_platform_admin(g.current_org_id)
    # admin_required is org-scoped admin, not platform_admin — restrict to the
    # current org (tenant-scoping-ok: fixes cross-org role-escalation IDOR).
    user = User.query.filter_by(id=user_id, organization_id=g.current_org_id).first_or_404()

    new_role = request.form.get("enterprise_role")

    if new_role not in VALID_ROLES:
        flash(f"Invalid role: {new_role}", "error")
        return redirect(url_for("user_role.edit_user_role", user_id=user_id))

    old_role = user.enterprise_role
    user.enterprise_role = new_role
    db.session.commit()

    flash(f"Role updated: {old_role or 'None'} → {new_role}", "success")
    return redirect(url_for("admin.user_info", user_id=user_id))
