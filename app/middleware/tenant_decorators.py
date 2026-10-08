"""
Tenant-aware authorization decorators.

@org_admin_required — user must be authenticated + is_org_admin for their org
@platform_admin_required — user must be authenticated + is_platform_admin
"""

from functools import wraps

from flask import abort, jsonify, request
from flask_login import current_user, login_required


def _wants_json():
    return (
        "/api/" in request.path
        or request.content_type == "application/json"
        or request.accept_mimetypes.best == "application/json"
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )


def org_admin_required(f):
    """Require authenticated user who is an org admin."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not getattr(current_user, "is_org_admin", False):
            if _wants_json():
                return jsonify({"error": "Organization admin access required"}), 403
            abort(403)
        return f(*args, **kwargs)
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
    decorator below, and require_org_or_platform_admin further down, which
    app/modules/admin/team_routes.py's platform-admin branch now calls
    through rather than re-typing — shares one implementation rather than
    each re-typing the same two-flag check.
    """
    from app.models import Permission

    is_flagged_platform_admin = getattr(user, "is_platform_admin", False)
    has_administer_permission = user.can(Permission.ADMINISTER)
    return bool(is_flagged_platform_admin and has_administer_permission)


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


def require_org_or_platform_admin(org_id):
    """Abort 403 unless the current user is this org's admin or a platform
    admin.

    A plain, importable function rather than a decorator: every call site
    needs it as an inline guard with an explicit ``org_id`` argument --
    usually ``g.current_org_id``, but a caller may also pass a path
    parameter's org_id directly (see app/modules/admin/team_routes.py,
    whose own private copy this replaces). Lives here, alongside
    ``is_platform_admin``/``platform_admin_required``, which it already
    depends on, so every caller that needs this exact two-vocabulary check
    (platform-wide admin OR this organisation's own admin) shares one
    implementation rather than each hand-writing the same
    ``if not (is_platform_admin(current_user) or
    rbac_service.is_org_admin(current_user, org_id)): abort(403)`` block.
    """
    from app.services.rbac_service import rbac_service

    if is_platform_admin(current_user):
        return
    if rbac_service.is_org_admin(current_user, org_id):
        return
    abort(403)
