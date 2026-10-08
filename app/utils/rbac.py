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
    """Map the current *user* to a role name in ``_ROLE_HIERARCHY``."""
    if getattr(user, "is_platform_admin", False):
        return "super_admin"
    # org_admin derives from Permission.ADMINISTER (is_admin()),
    # not from the denormalised is_org_admin column.
    try:
        if user.is_admin():
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

    # Legacy role via Role model (permissions bitfield 0xFF = admin)
    role_obj = getattr(user, "role", None)
    if role_obj and getattr(role_obj, "permissions", 0) == 0xFF:
        return "super_admin"

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
