"""
Team management routes — org member listing and role management (COM-007).

Blueprint: team_bp  |  URL prefix: /admin  |  All routes: org_admin or platform admin

``rbac_service.require_role("org_admin")`` reads only the per-org OrgRole
table (see app/services/rbac_service.py), a vocabulary separate from
platform-wide admin status (``app.middleware.tenant_decorators.is_platform_admin``
— ``current_user.is_platform_admin`` combined with ``Permission.ADMINISTER``,
the same pair ``platform_admin_required`` checks). A platform admin with no
OrgRole row for their org defaults to "viewer" there and was refused every
route below. ``tenant_decorators.require_org_or_platform_admin`` admits
either, reusing both existing checks rather than adding a third: the
platform-admin half calls ``tenant_decorators.is_platform_admin`` directly
(the predicate ``platform_admin_required`` itself now calls, so there is
exactly one implementation of it), and the org half calls
``rbac_service.is_org_admin`` unchanged. This file used to carry its own
private copy of that guard (``_require_org_or_platform_admin``); it now
imports the shared, public version instead, which every other admin route
needing the same two-vocabulary check also calls.

``app/utils/rbac.py``'s ``require_role("org_admin")`` was considered and not
used here: its role comes from ``User.is_org_admin`` / ``User.is_platform_admin``
booleans, not the ``OrgRole`` table this blueprint's own writes maintain.
``team_change_role()`` below, and every invitation-acceptance path
(``app/modules/account/services/invitation_service.py`` and
``account_service.py``), write the ``OrgRole`` row AND call
``User.grant_org_admin()`` / ``revoke_org_admin()`` (app/models/user.py) so
a member promoted to org_admin through this page, or through an invitation,
answers the same way everywhere — ``rbac_service.is_org_admin``,
``user.is_org_admin``/``is_admin()``, and ``app/utils/rbac.py``'s check alike.
"""

import logging

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.flask_email import mail_available
from app.middleware.tenant_decorators import require_org_or_platform_admin
from app.models.user import ROLE_DISPLAY_NAMES, User
from app.models.org_role import OrgRole, VALID_ORG_ROLES
from app.services.rate_limiter import rate_limit
from app.services.rbac_service import rbac_service

logger = logging.getLogger(__name__)

team_bp = Blueprint("team", __name__)


def _require_org_id():
    """Return current user's org_id or abort 403."""
    org_id = getattr(current_user, "organization_id", None)
    if org_id is None:
        abort(403)
    return org_id


def _render_team(org_id, error=None, status=200):
    from app.modules.account.services import invitation_service

    invitations = invitation_service.invitations_for(org_id)
    # Accounts an invitation opened and nobody has taken up are listed as
    # invitations, not as members.
    members = [
        m for m in User.query.filter_by(organization_id=org_id).all()
        if not invitation_service.is_unactivated(m)
    ]
    has_account_ids = {
        row.user_id for row in invitations if not invitation_service.is_unactivated(row.user)
    }
    role_map = {
        m.id: rbac_service.get_user_role(org_id, m.id) for m in members
    }
    return render_template(
        "admin/team.html",
        members=members,
        role_map=role_map,
        valid_roles=VALID_ORG_ROLES,
        invitations=invitations,
        has_account_ids=has_account_ids,
        personas=[(p, ROLE_DISPLAY_NAMES.get(p, p)) for p in invitation_service.INVITABLE_PERSONAS],
        mail_is_available=mail_available(),
        invite_error=error,
    ), status


@team_bp.route("/team")
@login_required
def team():
    """List org members with their roles, and the invitations still open."""
    org_id = _require_org_id()
    require_org_or_platform_admin(org_id)
    return _render_team(org_id)


@team_bp.route("/team/invite", methods=["POST"])
@login_required
@rate_limit(20, "1m", methods=("POST",))  # SECURITY: each POST can send mail
def team_invite():
    """Invite someone into the org by e-mail.

    An address with no account gets an account in THIS organisation and a
    single-use link to set a password; the role is granted when they take it
    up. An existing user from elsewhere gets a pending invitation they accept
    after signing in. Neither grants a role or membership before acceptance,
    and duplicate open invitations are refused.
    """
    org_id = _require_org_id()
    require_org_or_platform_admin(org_id)
    email = (request.form.get("email") or "").strip().lower()
    role = request.form.get("role", "viewer")

    if not email:
        return _render_team(org_id, "Cannot invite: email required.", 400)
    if role not in VALID_ORG_ROLES:
        return _render_team(org_id, f"Cannot invite: invalid role '{role}'.", 400)

    from app.modules.account.services import invitation_service

    user = User.find_by_email(email)
    if user is None or invitation_service.is_unactivated(user):
        from app.services.billing_plans import PlanLimitReached

        try:
            _, delivered, error = invitation_service.invite_new_person(
                org_id, current_user, email, org_role=role,
                persona=request.form.get("persona") or None,
            )
        except invitation_service.InvitationError as exc:
            db.session.rollback()
            return _render_team(org_id, exc.message, exc.status)
        except PlanLimitReached as exc:
            # Opening the invitee's account would take the organisation past
            # its plan; nothing is created and nothing is sent.
            db.session.rollback()
            return _render_team(org_id, str(exc), 402)
        if delivered:
            flash(f"Invitation sent to {email}.", "success")
        else:
            flash(f"The invitation to {email} could not be sent: {error} Use Resend to try again.", "error")
        return redirect(url_for("team.team"))

    if OrgRole.get_role(org_id, user.id) is not None:
        return _render_team(org_id, "This user is already a member of the organisation.", 409)

    try:
        _, delivered, error = invitation_service.invite_existing_account(
            org_id, current_user, user, role
        )
    except invitation_service.InvitationError as exc:
        db.session.rollback()
        return _render_team(org_id, exc.message, exc.status)
    # This person already has an account: the e-mailed link asks them to
    # sign in and accept. Without e-mail nobody can tell them, and the admin
    # is told exactly that.
    if delivered:
        flash(f"Invitation sent to {email}. They already have an account and accept it after signing in.", "success")
    elif delivered is None:
        flash(
            f"{email} already has an account. E-mail is not available on this server, "
            "so they have not been told about the invitation.",
            "error",
        )
    else:
        flash(f"The invitation to {email} could not be sent: {error} Use Resend to try again.", "error")
    return redirect(url_for("team.team"))


@team_bp.route("/team/invitations/<int:invitation_id>/resend", methods=["POST"])
@login_required
@rate_limit(20, "1m", methods=("POST",))  # SECURITY: each POST sends mail
def team_invitation_resend(invitation_id):
    """Send a fresh link for an open invitation; the previous link stops working."""
    org_id = _require_org_id()
    require_org_or_platform_admin(org_id)
    from app.modules.account.services import invitation_service

    try:
        row, delivered, error = invitation_service.resend(org_id, current_user, invitation_id)
    except invitation_service.InvitationError as exc:
        db.session.rollback()
        if exc.status == 404:
            abort(404)
        return _render_team(org_id, exc.message, exc.status)
    if delivered:
        flash(f"A new invitation link was sent to {row.user.email}.", "success")
    else:
        flash(f"The invitation to {row.user.email} could not be sent: {error}", "error")
    return redirect(url_for("team.team"))


@team_bp.route("/team/invitations/<int:invitation_id>/revoke", methods=["POST"])
@login_required
def team_invitation_revoke(invitation_id):
    """Withdraw an invitation so its link stops working.

    An account the invitation opened, and that nobody took up, is removed
    with it, so the address is free to register or be invited elsewhere.
    """
    org_id = _require_org_id()
    require_org_or_platform_admin(org_id)
    from app.modules.account.services import invitation_service

    try:
        email = invitation_service.revoke(org_id, invitation_id)
    except invitation_service.InvitationError:
        db.session.rollback()
        abort(404)
    flash(f"The invitation to {email} was withdrawn.", "success")
    return redirect(url_for("team.team"))


@team_bp.route("/team/role", methods=["POST"])
@login_required
def team_change_role():
    """Change a member's role within the org."""
    org_id = _require_org_id()
    require_org_or_platform_admin(org_id)
    user_id = request.form.get("user_id", type=int)
    role = request.form.get("role", "")

    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    if role not in VALID_ORG_ROLES:
        return jsonify({"error": f"invalid role '{role}'"}), 400

    user = db.session.get(User, user_id)
    if user is None or user.organization_id != org_id:
        abort(404)

    OrgRole.set_role(org_id, user_id, role, granted_by_id=current_user.id)
    # Keep the User.role (Permission.ADMINISTER authority) in step with the
    # OrgRole grant so every surface that answers "is this user an org admin"
    # sees the same answer.  This route only ever reaches a user whose own
    # organization_id equals org_id (checked above), so this is always a
    # grant/revoke in the user's own organisation.
    if role == "org_admin":
        user.grant_org_admin()
    elif user.is_admin():
        user.revoke_org_admin()
    db.session.commit()
    return redirect(url_for("team.team"))


@team_bp.route("/team/member/<int:user_id>", methods=["DELETE"])
@login_required
def team_remove_member(user_id):
    """Remove a user's org role (does not delete the user account)."""
    org_id = _require_org_id()
    require_org_or_platform_admin(org_id)

    # Prevent org_admin from removing themselves
    if user_id == current_user.id:
        return jsonify({"error": "Cannot remove yourself from the org"}), 400

    record = OrgRole.query.filter_by(
        organization_id=org_id, user_id=user_id
    ).first()
    if record:
        db.session.delete(record)
        # Revoke the Administrator role for a user being removed from this
        # organisation (a no-op for a platform admin — see
        # User.revoke_org_admin), so rbac_service.is_org_admin() (which falls
        # back to user.is_admin() for the user's own org) no longer answers
        # True after membership is deleted.
        user = db.session.get(User, user_id)
        if user is not None and user.is_admin():
            user.revoke_org_admin()
        db.session.commit()
    return jsonify({"status": "removed"})
