"""
Tenant-aware authorization decorators.

@org_admin_required — user must be authenticated + is_org_admin for their org
@platform_admin_required — user must be authenticated + is_platform_admin
"""

from functools import wraps

from flask import abort, g, jsonify, request
from flask_login import current_user, login_required


def _wants_json():
    return (
        "/api/" in request.path
        or request.content_type == "application/json"
        or request.accept_mimetypes.best == "application/json"
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )


def org_admin_required(f):
    """Require authenticated user who is an org admin of the ACTIVE organisation.

    Resolves directly via ``rbac_service.is_org_admin(current_user,
    g.current_org_id)`` rather than the ``current_user.is_org_admin``
    property, which only ever answers for the user's HOME organisation. See
    the admin-rbac-active-org PR description for the full rationale.
    ``@login_required`` sits inside this decorator's own wraps, so an
    anonymous request never reaches the check below.
    """
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        from app.services.rbac_service import rbac_service

        active_org_id = getattr(g, "current_org_id", None)
        if not (
            is_platform_admin(current_user)
            or rbac_service.is_org_admin(current_user, active_org_id)
        ):
            if _wants_json():
                return jsonify({"error": "Organization admin access required"}), 403
            abort(403)
        return f(*args, **kwargs)

    # Discoverability marker for tests/test_admin_rbac_active_org_enforcement.py's
    # url_map-wide sweep -- see the matching comment on admin_required
    # (app/_decorators_base.py) for why this survives further stacking.
    decorated._active_org_rbac_gate = "org_admin_required"

    return decorated


def is_platform_admin(user):
    """The one predicate for "is this user a platform admin" (cross-org access).

    Capgemini dry-run DEF-036: a demo tenant's org-admin (is_org_admin, not
    is_platform_admin) reached /admin/organizations and every route
    ``platform_admin_required`` guards, listing every tenant on the instance
    (including a real customer's users, emails and Make Admin/Deactivate/
    Delete controls) while the same account correctly got 403 from /admin/,
    /admin/users and other routes gated by Permission.ADMINISTER (a separate
    authz vocabulary — see CLAUDE.md's "Three authz vocabularies" note).
    Requiring both closes the gap regardless of which flag a given account
    was seeded with, and never weakens access for an account provisioned
    with both, which is how a real platform admin is meant to be set up.

    Extracted so every caller that needs this exact predicate — the
    decorator below, and app/modules/admin/team_routes.py's platform-admin
    branch — shares one implementation rather than each re-typing the same
    two-flag check.
    """
    from app.models import Permission

    is_flagged_platform_admin = getattr(user, "is_platform_admin", False)
    has_administer_permission = user.can(Permission.ADMINISTER)
    return bool(is_flagged_platform_admin and has_administer_permission)


def is_active_org_admin(user=None) -> bool:
    """The one predicate for "is this user an admin of the ACTIVE organisation".

    R2-1/R2-2 (PR 428 round 3): the same judgement ``org_admin_required``
    above already makes, extracted so inline checks scattered across route
    modules can call it instead of re-typing (or mistyping, as an
    unparenthesized ``current_user.is_admin`` bound-method reference did)
    the active-org-vs-home-org distinction themselves. Mirrors
    ``solutions_strategic.v2.routes.solution_design_routes._is_active_org_admin``,
    which predates this shared copy and is left as-is there.
    """
    user = user if user is not None else current_user
    if not getattr(user, "is_authenticated", False):
        return False
    from app.services.rbac_service import rbac_service

    active_org_id = getattr(g, "current_org_id", None)
    return is_platform_admin(user) or rbac_service.is_org_admin(user, active_org_id)


def platform_admin_required(f):
    """Require authenticated user who is a platform admin (cross-org access).

    See ``is_platform_admin`` above for the predicate and why it checks both
    flags.
    """
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not is_platform_admin(current_user):
            if _wants_json():
                return jsonify({"error": "Platform admin access required"}), 403
            abort(403)
        return f(*args, **kwargs)
    return decorated
