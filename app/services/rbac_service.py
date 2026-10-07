"""
RBACService — org-scoped role-based access control (COM-007).

Role hierarchy: org_admin (2) > architect (1) > viewer (0).

Usage:
    from app.services.rbac_service import rbac_service

    @rbac_service.require_role('org_admin')
    def my_view():
        ...
"""

import functools

from flask import abort
from flask_login import current_user

ROLE_HIERARCHY = {
    "org_admin": 2,
    "architect": 1,
    "viewer": 0,
}


class MemberAccessError(Exception):
    """A member's access could not be reduced. ``status`` is the HTTP answer."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


class RBACService:
    """Service for evaluating org-scoped RBAC permissions."""

    def get_user_role(self, org_id, user_id):
        """Return the user's role in the org. Defaults to 'viewer' if no row exists."""
        from app.models.org_role import OrgRole

        role = OrgRole.get_role(org_id, user_id)
        return role if role else "viewer"

    def is_org_admin(self, user, org_id):
        """True if ``user`` is an org_admin in ``org_id``.

        This is the one check every caller uses for "is this user an
        organisation administrator of this organisation" — including
        ``User.is_org_admin`` for the user's own organisation, which
        delegates to this function rather than computing its own answer, so
        the two can never disagree.

        Checks the per-organisation OrgRole table first: a person who
        belongs to several organisations (an invitation accepted into a
        foreign organisation) needs that answered per organisation, and
        OrgRole is where that grant lives. Only when no OrgRole row says
        otherwise does it fall back to the canonical Administrator-role
        authority (``user.is_admin()``) — and only for the user's OWN
        organisation: the Administrator role is global to the user, not
        scoped to one organisation, so a foreign-organisation OrgRole grant
        must never make this answer True for the user's own organisation.

        Takes the ``user`` object itself (not an id to re-query), so a grant
        or revoke made earlier in the same request or test is seen
        immediately rather than through a fresh, possibly stale read.
        """
        if self.get_user_role(org_id, user.id) == "org_admin":
            return True
        if user.organization_id == org_id and user.is_admin():
            return True
        return False

    def revoke_member_access(self, org_id, user_id, *, actor_id, reason, end_sessions):
        """The one place a member's access to ``org_id`` is reduced (R1-B88).

        Deletes the member's OrgRole row (they then answer as a viewer) and
        takes away organisation-admin authority they held. With
        ``end_sessions`` it also signs them out everywhere
        (``session_registry.revoke_all_for_user``, ``reason`` at most 32
        characters) and records an ``access_restricted`` audit row; the Team
        page's Remove button passes ``end_sessions=False`` and behaves as it
        always did.

        Refuses the actor's own account (400), with ``end_sessions`` a platform
        administrator (403), and a user who has no place in this organisation (404, the same answer
        as a missing id). Does not delete the account.
        Returns True when an OrgRole row was removed.

        When PR 424 lands, the export-flag path calls ``deactivate_user`` here.
        """
        from app import db
        from app.models.org_role import OrgRole
        from app.models.user import User

        if user_id == actor_id:
            raise MemberAccessError("You cannot reduce your own access.", 400)
        # tenant-scoping-ok: User is not TenantMixin; the organisation is checked below.
        user = User.query.filter_by(id=user_id).first()
        record = OrgRole.query.filter_by(organization_id=org_id, user_id=user_id).first()
        in_this_org = user is not None and (user.organization_id == org_id or record is not None)
        if end_sessions:
            # Signing someone out everywhere is only for people whose home is this organisation.
            in_this_org = user is not None and user.organization_id == org_id
        if not in_this_org:
            raise MemberAccessError("Member not found.", 404)
        if end_sessions and getattr(user, "is_platform_admin", False):
            # The Team page's Remove button never refused one (revoke_org_admin
            # leaves a platform admin's authority alone); the restricting paths do.
            raise MemberAccessError("A platform administrator's access is not managed here.", 403)

        removed = record is not None
        if record is not None:
            db.session.delete(record)
        # Revoke the Administrator role for a user being removed from this
        # organisation (a no-op for a platform admin -- see User.revoke_org_admin),
        # so is_org_admin() no longer answers True after the grant is gone.
        if (removed or end_sessions) and user.is_admin():
            user.revoke_org_admin()
        db.session.commit()

        if end_sessions:
            from app.models.audit_log import AuditLog
            from app.services.session_registry import revoke_all_for_user

            AuditLog.log(
                action="access_restricted",
                table_name="org_roles",
                record_id=user_id,
                organization_id=org_id,
                user_id=actor_id,
                extra_json={"member_id": user_id, "reason": reason, "role_removed": removed},
            )
            revoke_all_for_user(user_id, reason[:32])
        return removed

    def can_edit(self, org_id, user_id):
        """True if role is org_admin or architect (hierarchy level >= 1)."""
        role = self.get_user_role(org_id, user_id)
        return ROLE_HIERARCHY.get(role, 0) >= ROLE_HIERARCHY["architect"]

    def can_view(self, org_id, user_id):
        """True for all authenticated users — viewer is the minimum role."""
        return True

    def require_role(self, min_role):
        """
        Flask decorator factory that enforces a minimum org role.

        Returns 403 if current_user is not authenticated or their role is below min_role.

        Example:
            @app.route('/admin/settings')
            @login_required
            @rbac_service.require_role('org_admin')
            def admin_settings():
                ...
        """

        def decorator(f):
            @functools.wraps(f)
            def wrapper(*args, **kwargs):
                if not current_user.is_authenticated:
                    abort(403)
                org_id = getattr(current_user, "organization_id", None)
                if org_id is None:
                    abort(403)
                if min_role == "org_admin":
                    if not self.is_org_admin(current_user, org_id):
                        abort(403)
                else:
                    actual_role = self.get_user_role(org_id, current_user.id)
                    min_level = ROLE_HIERARCHY.get(min_role, 0)
                    actual_level = ROLE_HIERARCHY.get(actual_role, 0)
                    if actual_level < min_level:
                        abort(403)
                return f(*args, **kwargs)

            return wrapper

        return decorator


rbac_service = RBACService()
