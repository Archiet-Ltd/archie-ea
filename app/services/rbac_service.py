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

    def org_ids_for(self, user):
        """Every organisation ``user`` belongs to: the home organisation
        (if any) plus every ``OrgRole`` row, with no active-state
        filtering at all.

        Deactivation is not enforced at login or at session-switch time --
        neither ``app.middleware.tenant_context.user_can_access_org`` nor
        ``account_service.switch_active_organization`` checks
        ``Organization.is_active`` -- so a deactivated organisation can
        still be switched into today exactly like an active one. A caller
        that uses this to decide MFA authority (``is_org_admin_anywhere``
        below, via ``mfa_service.required_for``) must fail closed on that
        fact: an administrator of a deactivated organisation is still an
        administrator of a place they can still reach, so this reader must
        not pretend otherwise by excluding it. A caller that genuinely
        needs active-only organisations should query ``Organization``
        directly through its own, explicitly named function rather than
        filtering this one.
        """
        from app.models.org_role import OrgRole

        ids = set()
        if user.organization_id is not None:
            ids.add(user.organization_id)
        rows = OrgRole.query.filter(OrgRole.user_id == user.id).all()
        ids.update(row.organization_id for row in rows)
        return ids

    def is_org_admin_anywhere(self, user):
        """True when ``user`` administers any organisation they belong to
        (home organisation or an invited one via ``OrgRole``), with no
        active-state filtering -- see ``org_ids_for`` above.

        Single-query reduction of "is ``user`` an org_admin of any
        organisation in ``org_ids_for(user)``": ``is_org_admin`` only ever
        answers True for an organisation via one of two routes -- an
        ``OrgRole`` row of role ``"org_admin"`` for that organisation (which
        covers every organisation reachable at all, home or foreign, since
        every such row's organisation is already included in
        ``org_ids_for``), or the user's own home organisation plus
        ``user.is_admin()`` (the Administrator-role case, which carries no
        ``OrgRole`` row). Checking for the existence of either reduces this
        to one ``OrgRole`` query plus one cheap boolean check, instead of
        looping over every id ``org_ids_for`` returns and re-querying
        ``is_org_admin`` per id.

        Used where "is this user an administrator of anything" must be
        answered regardless of which specific organisation granted it --
        e.g. ``mfa_service.required_for``, which must require MFA for an
        administrator invited into a foreign organisation exactly as it
        does for a home-organisation administrator."""
        from app.models.org_role import OrgRole

        has_org_admin_row = (
            OrgRole.query.filter_by(user_id=user.id, role="org_admin").first()
            is not None
        )
        if has_org_admin_row:
            return True
        return user.organization_id is not None and user.is_admin()

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
