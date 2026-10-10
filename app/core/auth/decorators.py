"""
Consolidated authentication and authorization decorators.

This is the canonical location for all auth decorators. Existing source files
(app/decorators.py, app/auth/decorators.py, app/utils/decorators.py) remain
for backward compatibility but should import from here for new code.

Decorators:
- login_required — JSON-aware wrapper around Flask-Login
- requires_permission — granular permission check (string-based)
- permission_required — Permission enum-based check
- require_auth — simple authentication check (no role/permission)
- require_feature — feature flag gate
- require_roles — role-based access control
- audit_log — no-op audit decorator (compatibility stub)

D-5 (admin-rbac-active-org continuation): this module used to also define
its own ``admin_required`` -- one of three separate implementations in this
codebase, and the one that never carried the active-org fix
``admin_required``/``org_admin_required`` get elsewhere in this PR (it was
only ``permission_required(Permission.ADMINISTER)``, a bare global-flag
check). Removed; its one caller (app/main/views.py, three platform-wide
settings routes already additionally gated by ``platform_admin_required``)
now imports the canonical, fixed implementation from ``app.decorators``
instead.
"""

from functools import wraps

from flask import abort, current_app, jsonify
from flask_login import current_user
from flask_login import login_required as flask_login_required


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def login_required(f):
    """Wrapper around Flask-Login with custom JSON error handling.

    Source: app/auth/decorators.py
    """

    @wraps(f)
    @flask_login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return (
                jsonify(
                    {
                        "error": "Authentication required",
                        "message": "You must be logged in to access this resource",
                    }
                ),
                401,
            )
        return f(*args, **kwargs)

    return decorated_function


def require_auth(f):
    """Require user authentication (abort-based, no JSON).

    Source: app/decorators.py
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)
        return f(*args, **kwargs)

    return decorated_function


# ---------------------------------------------------------------------------
# Permission / Role checks
# ---------------------------------------------------------------------------


def requires_permission(permission_name):
    """Check if current user has a specific permission (string-based).

    Source: app/auth/decorators.py
    """

    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if not current_user.has_permission(permission_name):
                return (
                    jsonify(
                        {
                            "error": "Insufficient permissions",
                            "message": f'You need "{permission_name}" permission',
                            "required_permission": permission_name,
                        }
                    ),
                    403,
                )
            return f(*args, **kwargs)

        return decorated_function

    return decorator


def permission_required(permission):
    """Restrict a view to users with the given Permission enum.

    Source: app/decorators.py
    """

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.can(permission):
                abort(403)
            return f(*args, **kwargs)

        return decorated_function

    return decorator


def require_roles(*allowed_roles):
    """Require user to have one of the specified roles.

    Args:
        *allowed_roles: Role names (strings) the user must have at least one of.

    Source: app/decorators.py
    """

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)

            user_roles = set()

            if hasattr(current_user, "roles"):
                for role in current_user.roles:
                    if hasattr(role, "name"):
                        user_roles.add(role.name.lower())
                    elif isinstance(role, str):
                        user_roles.add(role.lower())

            if hasattr(current_user, "role_names"):
                user_roles.update(r.lower() for r in current_user.role_names)

            if hasattr(current_user, "role"):
                user_roles.add(str(current_user.role).lower())

            archetype = getattr(current_user, "role_archetype", None)
            if archetype and str(archetype).lower() != "admin":
                user_roles.add(str(archetype).lower())

            required = set(r.lower() for r in allowed_roles)

            if not user_roles & required:
                current_app.logger.warning(
                    f"Access denied to {f.__name__}: user "
                    f"{getattr(current_user, 'username', None) or current_user.id} "
                    f"has roles {user_roles}, required {required}"
                )
                abort(403)

            return f(*args, **kwargs)

        return decorated_function

    return decorator


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------


def require_feature(feature_key: str, fallback_enabled: bool = True):
    """Require a feature flag to be enabled to access a route.

    Args:
        feature_key: The feature flag key to check.
        fallback_enabled: If True, allow access when flag doesn't exist.

    Source: app/decorators.py
    """

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            from app.models.feature_flags import FeatureFlag

            feature = FeatureFlag.query.filter_by(key=feature_key).first()

            if feature is None:
                if not fallback_enabled:
                    current_app.logger.warning(
                        f"Feature flag '{feature_key}' not found, "
                        "denying access (fallback_enabled=False)"
                    )
                    abort(404)
            elif not feature.is_active:
                current_app.logger.info(
                    f"Feature flag '{feature_key}' is disabled, "
                    f"denying access to {f.__name__}"
                )
                abort(404)

            return f(*args, **kwargs)

        return decorated_function

    return decorator


# ---------------------------------------------------------------------------
# Audit (compatibility stub)
# ---------------------------------------------------------------------------


def audit_log(action_name: str):
    """Audit logging decorator — delegates to canonical app/decorators.py.

    Logs action, user, entity info, and request metadata to AuditLog.
    """
    from app.decorators import audit_log as _canonical_audit_log
    return _canonical_audit_log(action_name)
