"""
RBAC decorators — lightweight route-level access control.

These decorators work alongside the existing ``app/services/rbac_service.py``
(COM-007) without modifying it.  They are pure Flask route decorators that
use ``functools.wraps`` and return structured JSON 403 responses so that
API clients receive machine-readable errors.

Role hierarchy (lowest → highest privilege):
    viewer  <  architect  <  org_admin  <  super_admin

Usage
-----
    from app.utils.rbac import require_login, require_role, require_org_membership

    @bp.route("/admin/settings")
    @require_login()
    @require_role("org_admin")
    def admin_settings():
        ...

    @bp.route("/orgs/<int:org_id>/data")
    @require_login()
    @require_org_membership()
    def org_data(org_id):
        ...
"""

import functools
import logging

from flask import g, jsonify, redirect, url_for

logger = logging.getLogger(__name__)

# Ordered from lowest to highest privilege.
_ROLE_HIERARCHY = ["viewer", "architect", "org_admin", "super_admin"]


def _get_user_role(user):
    """Map the current *user* to a role name in ``_ROLE_HIERARCHY``.

    D-4 (admin-rbac-active-org continuation): ``org_admin`` used to derive
    from ``user.is_admin()`` -- a global ``Permission.ADMINISTER`` flag,
    independent of which organisation is active in the session
    (``g.current_org_id``). Since every self-registered user is
    Administrator of their own organisation, a user who merely accepted a
    Viewer invitation into another organisation and switched their session
    into it was mapped to ``org_admin`` (and so passed every
    ``@require_role("org_admin")`` check) there too -- the exact bug
    ``admin_required``/``org_admin_required`` already fix elsewhere in this
    PR. Also switched the platform-admin check to the canonical
    ``is_platform_admin`` predicate (both the flag AND
    ``Permission.ADMINISTER``), matching ``app/middleware/tenant_decorators.
    py`` rather than re-deriving a slightly looser version of the same
    check from the bare column.

    The pre-fix "legacy role via Role model (permissions bitfield 0xFF)"
    fallback below used to run UNCONDITIONALLY whenever the (correctly
    active-org-scoped) ``is_org_admin`` check above returned False -- not
    only when it raised, despite the comment that used to sit on it. That
    silently reintroduced the exact bug this function otherwise fixes: it
    re-derives "org_admin" from the same global
    ``Role.permissions == Permission.ADMINISTER`` bitfield for every ordinary
    non-admin request, for any user whose Role happens to be "Administrator"
    (every self-registered user, in their own organisation) -- confirmed by
    instrumenting this function directly, which is how this got caught
    before landing. Removed outright: the active-org check above is the one
    source of truth for "org_admin" now, with no legacy bypass behind it.
    """
    from app.middleware.tenant_decorators import is_platform_admin
    from app.services.rbac_service import rbac_service

    if is_platform_admin(user):
        return "super_admin"
    try:
        active_org_id = getattr(g, "current_org_id", None)
        if rbac_service.is_org_admin(user, active_org_id):
            return "org_admin"
    except Exception:  # noqa: BLE001
        pass

    er = (getattr(user, "enterprise_role", "") or "").lower()
    _ARCHITECT_ROLES = {
        "solution_architect",
        "enterprise_architect",
        "arb_member",
        "portfolio_manager",
    }
    if er in _ARCHITECT_ROLES:
        return "architect"

    return "viewer"


def require_login():
    """Redirect unauthenticated requests to the login page."""

    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            from flask_login import current_user

            if not current_user.is_authenticated:
                try:
                    return redirect(url_for("auth.login"))
                except Exception:
                    return jsonify({"error": "Authentication required"}), 401
            return f(*args, **kwargs)

        return wrapper

    return decorator


def require_role(*roles):
    """Return 403 JSON unless the current user holds one of *roles* (or higher).

    Example — require at least org_admin::

        @require_role("org_admin")

    Example — require either architect or org_admin (not viewer)::

        @require_role("architect", "org_admin", "super_admin")

    If no *roles* are supplied the decorator is a no-op (all logged-in users
    are permitted).
    """

    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            from flask_login import current_user

            if not current_user.is_authenticated:
                return jsonify({"error": "Authentication required", "code": 401}), 401

            if not roles:
                return f(*args, **kwargs)

            user_role = _get_user_role(current_user)
            user_level = _ROLE_HIERARCHY.index(user_role) if user_role in _ROLE_HIERARCHY else 0

            # Build the minimum required level from the supplied roles.
            required_level = min(
                _ROLE_HIERARCHY.index(r) for r in roles if r in _ROLE_HIERARCHY
            ) if any(r in _ROLE_HIERARCHY for r in roles) else 0

            if user_level < required_level:
                logger.warning(
                    "RBAC: user %s (role=%s) denied access to %s — requires %s",
                    current_user.id,
                    user_role,
                    f.__name__,
                    roles,
                )
                return (
                    jsonify(
                        {
                            "error": "Insufficient permissions",
                            "required_roles": list(roles),
                            "your_role": user_role,
                        }
                    ),
                    403,
                )

            return f(*args, **kwargs)

        # R2-5 (PR 428 round 3): discoverability marker for the url_map
        # sweep (tests/test_admin_rbac_active_org_enforcement.py), set only
        # when this instance actually requires more than the "viewer"
        # floor -- a no-op require_role() (or one that only ever asks for
        # "viewer") passes every authenticated user by design, and marking
        # that would make the sweep expect a 403 the decorator was never
        # meant to produce.
        _required_level = (
            min(_ROLE_HIERARCHY.index(r) for r in roles if r in _ROLE_HIERARCHY)
            if any(r in _ROLE_HIERARCHY for r in roles)
            else 0
        )
        if _required_level > 0:
            wrapper._active_org_rbac_gate = "require_role"

        return wrapper

    return decorator


def require_org_membership():
    """Return 403 JSON if the current user does not belong to the request org.

    The decorator compares ``current_user.organization_id`` against
    ``g.current_org_id`` (set by the tenant-context middleware).  If
    ``g.current_org_id`` is not set the check is skipped (system context).
    """

    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            from flask_login import current_user

            if not current_user.is_authenticated:
                return jsonify({"error": "Authentication required", "code": 401}), 401

            request_org = getattr(g, "current_org_id", None)
            if request_org is None:
                return f(*args, **kwargs)

            user_org = getattr(current_user, "organization_id", None)
            if user_org != request_org:
                logger.warning(
                    "RBAC: user %s (org=%s) denied cross-org access to org %s",
                    current_user.id,
                    user_org,
                    request_org,
                )
                return (
                    jsonify(
                        {
                            "error": "Cross-organisation access denied",
                            "your_org": user_org,
                            "requested_org": request_org,
                        }
                    ),
                    403,
                )

            return f(*args, **kwargs)

        return wrapper

    return decorator
