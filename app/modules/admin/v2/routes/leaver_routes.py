"""Leaver transfer list (R1-B26 PR 1, TB-0143).

Shows each deactivated user of the caller's organisation with every
application and element they still own, and lets an administrator move each
ownership to another active user through the one ownership record. Deactivation
itself changes no ownership row; nothing is reassigned automatically.

The routes are registered on the admin blueprint (``admin``, prefix ``/admin``).
"""

from __future__ import annotations

from flask import abort, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.decorators import admin_required
from app.services import provisioning_service
from app.services.rate_limiter import rate_limit

from .admin_routes import admin_bp_v2


@admin_bp_v2.route("/leavers", methods=["GET"])
@login_required
@admin_required
def leavers():
    """Departed users of this organisation and what each still owns."""
    org_id = g.current_org_id
    return render_template(
        "admin/leavers.html",
        leavers=provisioning_service.leaver_ownerships(org_id),
        active_users=provisioning_service.active_users(org_id),
    )


@admin_bp_v2.route("/leavers/ownerships/<int:owner_id>/transfer", methods=["POST"])
@login_required
@admin_required
@rate_limit(10, "1m", methods=("POST",))
def leaver_transfer(owner_id):
    """Hand one ownership row of a departed user to an active user."""
    org_id = g.current_org_id
    try:
        _row, removed = provisioning_service.transfer_ownership(
            org_id, owner_id, request.form.get("new_owner_id"), actor=current_user.id
        )
    except provisioning_service.NotFoundError:
        abort(404)
    except provisioning_service.ProvisioningError as exc:
        flash(exc.detail, "error")
        return redirect(url_for("admin.leavers"))
    flash(
        "Ownership handed over. The new owner already held it, so the departed user's entry was removed."
        if removed else "Ownership handed over.",
        "success",
    )
    return redirect(url_for("admin.leavers"))
