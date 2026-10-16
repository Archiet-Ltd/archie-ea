"""
Role-Based Route Protection Decorator (NS-014)

Protects routes by requiring specific enterprise roles.
Returns 403 Forbidden if user lacks required role.

Part of North Star Persona MVP implementation.
ADR Reference: docs/adr/0009-persona-based-navigation.md
"""

from functools import wraps
from typing import List, Union

from flask import abort, current_app, request
from flask_login import current_user

from app.middleware.tenant_decorators import is_platform_admin
from app.models.user import ROLE_PLATFORM_ADMIN
from app.utils.role_access import get_user_role


# The roles that handle data-subject requests (scope, assign, access, erasure).
# The route decorators and the sidebar/directory link share this one list.
DATA_SUBJECT_REQUEST_ROLES = ["security_architect"]


def holds_administrator_authority(user):
    """True for a real administrator: an organisation administrator or a
    platform administrator (the one ``is_platform_admin`` predicate).

    The stored ``enterprise_role`` of ``platform_admin`` is a persona, and it is
    the column default for every user who never picked one, so it is never
    treated as authority here: a title decides what a person sees, authority
    decides what they may do."""
    return bool(getattr(user, "is_org_admin", False) or is_platform_admin(user))


def role_admitted(user, allowed_roles):
    """The one admission rule for ``requires_role``: a listed persona (other than
    platform_admin, which stands for administrator authority), or real
    administrator authority, which is always admitted."""
    persona = get_user_role(user)
    if persona != ROLE_PLATFORM_ADMIN and persona in allowed_roles:
        return True
    return holds_administrator_authority(user)


def may_handle_data_subject_requests(user):
    """True when ``user`` may open the data-subject request pages: the roles in
    DATA_SUBJECT_REQUEST_ROLES, or an administrator (as ``requires_role``
    always admits administrators).

    R3-5 (PR 428 round 4): genuine authority is judged by
    ``holds_administrator_authority`` (``is_org_admin``/``is_platform_admin``),
    never by the raw ``enterprise_role`` persona column happening to read
    the literal string "platform_admin" (that column's legacy default for
    every account). Otherwise this sidebar/directory check would keep
    showing the data-subject-request pages to a default-persona user that
    ``requires_role`` now correctly refuses."""
    return role_admitted(user, DATA_SUBJECT_REQUEST_ROLES)


def requires_role(allowed_roles: Union[str, List[str]]):
    """
    Decorator to restrict route access to specific enterprise roles.

    Args:
        allowed_roles: Single role or list of roles that can access the route.
                      Platform admin always has access.

    Usage:
        @app.route('/admin/settings')
        @login_required
        @requires_role(['platform_admin'])
        def admin_settings():
            ...

        @app.route('/procurement/contracts')
        @login_required
        @requires_role(['procurement', 'portfolio_manager', 'platform_admin'])
        def procurement_contracts():
            ...

    Returns:
        Decorated function that checks role before executing

    Raises:
        403 Forbidden if user lacks required role
    """
    # Normalize to list
    if isinstance(allowed_roles, str):
        allowed_roles = [allowed_roles]

    # Administrators (real authority, not the platform_admin title) always pass.
    allowed_roles = list(allowed_roles)

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Must be logged in
            if not current_user.is_authenticated:
                current_app.logger.warning(
                    f"Unauthenticated access attempt to {request.path}"
                )
                abort(401)

            # R3-5 (PR 428 round 4): "Always allow platform_admin" above
            # used to be implemented by appending the literal string
            # "platform_admin" to allowed_roles and comparing it against
            # get_user_role(current_user) -- the raw, uncomputed
            # ``enterprise_role`` persona column, which defaults to
            # "platform_admin" for every legacy account ("existing users
            # get full access", app/models/user.py). That let a default-
            # persona Viewer through every route this decorator guards,
            # all nine GDPR data-subject-request routes included, the same
            # "default persona stands in for platform authority" bug this
            # round already fixed in role_required/require_roles. Genuine
            # platform authority is judged the one real way, same as those
            # two decorators.
            from app.middleware.tenant_decorators import is_platform_admin

            if is_platform_admin(current_user):
                return f(*args, **kwargs)

            # Get user's role
            user_role = get_user_role(current_user)

            # Check if user has required role
            if not role_admitted(current_user, allowed_roles):
                current_app.logger.warning(
                    f"Access denied: user {current_user.id} (role={user_role}) "
                    f"attempted to access {request.path} (requires {allowed_roles})"
                )
                abort(403, description=f"Access denied. Required role: {', '.join(allowed_roles)}")

            return f(*args, **kwargs)

        return decorated_function

    return decorator


def requires_admin(f):
    """
    Shorthand decorator for admin-only routes.

    Usage:
        @app.route('/admin/users')
        @login_required
        @requires_admin
        def admin_users():
            ...
    """
    return requires_role([ROLE_PLATFORM_ADMIN])(f)


def requires_procurement(f):
    """
    Shorthand decorator for procurement routes.
    Allows procurement role and portfolio_manager (read-only context).
    """
    return requires_role(["procurement", "portfolio_manager"])(f)


def requires_procurement_or_finance(f):
    """
    Shorthand for the two procurement pages a finance persona also owns:
    licences and spend (R1-B36, TB-0146). Deliberately NOT applied to
    contracts, renewals or the compliance dashboard -- adding "finance" to
    the shared requires_procurement would have opened every procurement
    page to it, which the authorisation matrix caught as a real mismatch
    (POLICY only names the two pages finance's own sidebar links to).
    """
    return requires_role(["procurement", "portfolio_manager", "finance"])(f)


def requires_application_owner(f):
    """
    Shorthand decorator for application manager routes.
    """
    return requires_role(["application_manager"])(f)


def requires_any_architect(f):
    """
    Shorthand decorator for architect routes (SA, EA, or BA).
    """
    return requires_role(
        ["solution_architect", "enterprise_architect", "business_architect"]
    )(f)


def requires_governance(f):
    """
    Shorthand decorator for governance routes (ARB, EA, BA, CTO).
    """
    return requires_role(
        ["arb_member", "enterprise_architect", "business_architect", "cto"]
    )(f)
