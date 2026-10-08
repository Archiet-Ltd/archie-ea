from functools import wraps

from flask import abort, current_app, g
from flask_login import current_user

from app.models import Permission
import logging

logger = logging.getLogger(__name__)


def permission_required(permission):
    """Restrict a view to users with the given permission."""

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.can(permission):
                abort(403)
            return f(*args, **kwargs)

        return decorated_function

    return decorator


def admin_required(f):
    """Require Permission.ADMINISTER AND organisation-admin authority in the
    ACTIVE organisation (``g.current_org_id``), not merely a global Role flag.
    See the admin-rbac-active-org PR description for the full rationale.

    Built on ``permission_required(Permission.ADMINISTER)(f)`` rather than an
    inline re-check so ``tests/test_admin_route_authorisation.py``'s
    closure-walking ``_guarded()`` still finds ``Permission.ADMINISTER`` in
    the chain. The anonymous-user short-circuit below exists because
    ``rbac_service.is_org_admin`` reads ``user.id``/``user.organization_id``,
    which ``AnonymousUserMixin`` doesn't have -- without it, an anonymous
    request would 500 instead of getting today's 403 (or redirect, wherever
    ``@login_required`` sits above this decorator).
    """
    permission_gated = permission_required(Permission.ADMINISTER)(f)

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return permission_gated(*args, **kwargs)

        from app.middleware.tenant_decorators import is_platform_admin
        from app.services.rbac_service import rbac_service

        active_org_id = getattr(g, "current_org_id", None)
        if not (
            is_platform_admin(current_user)
            or rbac_service.is_org_admin(current_user, active_org_id)
        ):
            abort(403)
        return permission_gated(*args, **kwargs)

    # Discoverability marker for tests/test_admin_rbac_active_org_enforcement.py's
    # url_map-wide sweep: functools.wraps propagates __dict__ (and so this
    # attribute) up through however many further decorators are stacked on
    # top, so the marker survives on app.view_functions[endpoint] regardless
    # of stacking order or depth. Not read by any runtime code path -- test
    # introspection only.
    decorated_function._active_org_rbac_gate = "admin_required"

    return decorated_function


def governance_gate_reader_required(f):
    """Allow gate-policy readers without granting configuration authority.

    D-4 (admin-rbac-active-org continuation): the ``may_administer`` check
    used to be ``current_user.can(Permission.ADMINISTER)`` -- the same
    global-flag-not-active-org bug ``admin_required`` carried before this
    PR's main commit fixed it. This guards ``/admin/audit-log``, so a
    switched-org Viewer (home-org admin of their own organisation) could
    read and export another organisation's audit trail. Fixed the same way:
    resolve authority against ``g.current_org_id``.

    ``is_security_architect`` is a global persona flag
    (``current_user.enterprise_role``), not a per-organisation grant --
    there is no "security architect of this org" row to check against, so
    the same active-org switch would let that persona read every
    organisation's audit log too. Scoped to the user's own home
    organisation (the one place the persona is actually anchored) rather
    than removed outright, preserving the original intent (a security
    architect reads their own organisation's audit trail without needing
    org-admin standing there).
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(403)

        from app.middleware.tenant_decorators import is_platform_admin
        from app.services.rbac_service import rbac_service

        active_org_id = getattr(g, "current_org_id", None)
        is_org_admin_here = is_platform_admin(current_user) or rbac_service.is_org_admin(
            current_user, active_org_id
        )
        is_security_architect_of_this_org = (
            getattr(current_user, "enterprise_role", None) == "security_architect"
            and active_org_id is not None
            and active_org_id == getattr(current_user, "organization_id", None)
        )
        if not (is_org_admin_here or is_security_architect_of_this_org):
            abort(403)
        return f(*args, **kwargs)

    return decorated_function


def require_auth(f):
    """Require user authentication"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)
        return f(*args, **kwargs)

    return decorated_function


def require_feature(feature_key: str, fallback_enabled: bool = True):
    """Require a feature flag to be enabled to access a route.
    
    Args:
        feature_key: The feature flag key to check
        fallback_enabled: If True, allow access when flag doesn't exist (default: True)
                         If False, deny access when flag doesn't exist
    
    Usage:
        @require_feature('user_management')
        @admin.route('/admin/users')
        def users_list():
            ...
    
    When feature is disabled: Returns 404 (not 403) to hide existence
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Import here to avoid circular dependency
            from app.models.feature_flags import FeatureFlag
            
            # Check if feature flag exists and is enabled
            feature = FeatureFlag.query.filter_by(key=feature_key).first()
            
            if feature is None:
                # Feature doesn't exist - use fallback
                if not fallback_enabled:
                    current_app.logger.warning(
                        f"Feature flag '{feature_key}' not found, denying access (fallback_enabled=False)"
                    )
                    abort(404)  # Use 404 not 403 to hide feature existence
            elif not feature.is_active:
                # Feature exists but is disabled
                current_app.logger.info(
                    f"Feature flag '{feature_key}' is disabled, denying access to {f.__name__}"
                )
                abort(404)  # Use 404 not 403 to hide feature existence
            
            return f(*args, **kwargs)
        
        return decorated_function
    
    return decorator


def audit_log(action_name: str):
    """Audit logging decorator for route-level CRUD action tracking (ISS-006).

    Logs the action, user, entity info, and request metadata to AuditLog.
    Gracefully degrades if audit model import fails (e.g., during tests).

    Usage:
        @audit_log("application_create")
        def create_application():
            ...
    """

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            result = f(*args, **kwargs)

            # Best-effort audit — never break the request
            try:
                from app.models.audit_log import AuditLog
                from flask import request as _req, g
                from flask_login import current_user as _cu

                user_id = _cu.id if _cu and _cu.is_authenticated else None
                user_email = _cu.email if _cu and _cu.is_authenticated else None

                # Extract entity info from route kwargs or view args
                entity_id = kwargs.get("id") or kwargs.get("item_id") or kwargs.get("review_id")
                entity_type = action_name.rsplit("_", 1)[0] if "_" in action_name else action_name

                AuditLog.log(
                    action=action_name,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    user_id=user_id,
                    user_email=user_email,
                    ip_address=_req.remote_addr if _req else None,
                    description=f"{action_name} via {_req.path}" if _req else action_name,
                    status="success",
                    request_id=getattr(g, "request_id", None) if g else None,
                )
            except Exception as exc:
                logger.debug("suppressed error in audit_log.decorator.wrapper (app/_decorators_base.py): %s", exc)  # Never break the request for audit failures

            return result

        return wrapper

    return decorator


def role_required(*roles):
    """Restrict access to users whose ``enterprise_role`` is in *roles* (ENT-068).

    Falls back to active-org admin authority so existing admin users are
    never locked out. Must be placed **after** ``@login_required`` in the
    decorator stack.

    D-4 (admin-rbac-active-org continuation): the admin bypass used to be
    ``hasattr(current_user, "is_admin") and current_user.is_admin()`` -- a
    global ``Permission.ADMINISTER`` flag, independent of which organisation
    is active in the session (``g.current_org_id``). Since every
    self-registered user is Administrator of their own organisation, a user
    who merely accepted a Viewer invitation into another organisation and
    switched their session into it bypassed the ``enterprise_role`` check
    entirely there too -- the exact bug ``admin_required``/
    ``org_admin_required`` already fix elsewhere in this PR, reachable at
    every call site of this decorator (app/modules/applications/routes/
    coverage_routes.py, app/modules/architecture_assistant/routes/
    metamodel_property_routes.py, app/modules/capabilities/routes/
    ownership_routes.py).

    Usage::

        from app.decorators import role_required
        from app.models.user import ROLE_SOLUTION_ARCHITECT, ROLE_PLATFORM_ADMIN

        @app.route("/solutions/<int:id>/edit", methods=["POST"])
        @login_required
        @role_required(ROLE_SOLUTION_ARCHITECT, ROLE_PLATFORM_ADMIN)
        def edit_solution(id):
            ...
    """

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)

            from app.middleware.tenant_decorators import is_platform_admin
            from app.services.rbac_service import rbac_service

            active_org_id = getattr(g, "current_org_id", None)
            if is_platform_admin(current_user) or rbac_service.is_org_admin(
                current_user, active_org_id
            ):
                return f(*args, **kwargs)
            if not hasattr(current_user, "enterprise_role"):
                abort(403)
            if current_user.enterprise_role not in roles:
                abort(403)
            return f(*args, **kwargs)

        return decorated_function

    return decorator


def require_roles(*allowed_roles):
    """Require user to have one of the specified roles.
    
    Args:
        *allowed_roles: Role names (strings) the user must have at least one of
        
    Usage:
        @require_roles('admin', 'architect')
        def admin_endpoint():
            ...
    
    Returns:
        403 Forbidden if user lacks required roles
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)

            def _normalize_role_name(raw_role):
                if raw_role is None:
                    return None
                role_name = str(raw_role).strip().lower()
                if role_name.startswith("<role '") and role_name.endswith("'>"):
                    role_name = role_name[7:-2]
                role_name = role_name.replace(" ", "_")
                if role_name == "administrator":
                    return "admin"
                return role_name
            
            # Get user roles (handle different user model formats)
            user_roles = set()
            
            # Try getting roles as list of role objects
            if hasattr(current_user, 'roles'):
                for role in current_user.roles:
                    if hasattr(role, 'name'):
                        normalized = _normalize_role_name(role.name)
                        if normalized:
                            user_roles.add(normalized)
                        if hasattr(role, "index"):
                            normalized_index = _normalize_role_name(role.index)
                            if normalized_index:
                                user_roles.add(normalized_index)
                    elif isinstance(role, str):
                        normalized = _normalize_role_name(role)
                        if normalized:
                            user_roles.add(normalized)
            
            # Try getting roles as list of strings
            if hasattr(current_user, 'role_names'):
                user_roles.update(
                    normalized
                    for normalized in (
                        _normalize_role_name(r) for r in current_user.role_names
                    )
                    if normalized
                )
            
            # Try single role field
            if hasattr(current_user, 'role'):
                role_obj = current_user.role
                if hasattr(role_obj, "name"):
                    normalized = _normalize_role_name(role_obj.name)
                    if normalized:
                        user_roles.add(normalized)
                    if hasattr(role_obj, "index"):
                        normalized_index = _normalize_role_name(role_obj.index)
                        if normalized_index:
                            user_roles.add(normalized_index)
                else:
                    normalized = _normalize_role_name(role_obj)
                    if normalized:
                        user_roles.add(normalized)

            # enterprise_role is the persona system the product actually gates
            # navigation and dashboards on, but require_roles never looked at
            # it -- so a business_architect was 403ed by @require_roles("admin",
            # "architect") on the capability CRUD endpoints that exist for that
            # persona. Contribute the role itself, plus the coarse name it
            # stands for, so the two vocabularies agree.
            #
            # But ONLY for an account whose Role already carries permission.
            # enterprise_role says what someone DOES; Role says what they are
            # allowed to DO, and a persona label must never manufacture the
            # second from the first. Without this guard the read-only Viewer
            # role (permissions=0, added by A-03 precisely so an account can
            # read and never write) was defeated on every
            # @require_roles("admin", "architect") route: nearly every user
            # carries an enterprise_role because it drives the sidebar, so a
            # Viewer whose persona happened to end in "_architect" was handed
            # "architect" and could create and delete ArchiMate elements.
            # Caught by tests/test_r32_ai_permission_gate.py's V-04 regression
            # pair, which is exactly what those tests were written to hold.
            try:
                from app.models.user import Permission

                may_write = bool(current_user.can(Permission.GENERAL))
                may_administer = bool(current_user.can(Permission.ADMINISTER))
            except Exception:  # noqa: BLE001 - unusual user models stay as before
                may_write = True
                may_administer = True

            enterprise_role = _normalize_role_name(
                getattr(current_user, "enterprise_role", None)
            )
            if enterprise_role and may_write:
                user_roles.add(enterprise_role)
                if enterprise_role.endswith("_architect"):
                    user_roles.add("architect")
                elif enterprise_role == "platform_admin" and may_administer:
                    user_roles.add("admin")

            if hasattr(current_user, "role_archetype") and current_user.role_archetype:
                normalized_archetype = _normalize_role_name(current_user.role_archetype)
                if normalized_archetype:
                    user_roles.add(normalized_archetype)

            # D-4 (admin-rbac-active-org continuation): every path above that
            # can add "admin" to user_roles does so from a flag that is GLOBAL
            # to the user -- current_user.role.name == "Administrator" (every
            # self-registered user is Administrator of their own organisation),
            # current_user.roles/role_names carrying the same, or
            # enterprise_role == "platform_admin" paired with the global
            # Permission.ADMINISTER flag. None of those are scoped to
            # g.current_org_id, so the exact admin_required/org_admin_required
            # bug this PR fixes applied here too: a Viewer of the ACTIVE
            # organisation, home-org Administrator of their OWN organisation,
            # satisfied @require_roles("admin", ...) on every route guarding
            # bulk-delete, custom fields, ARB stages, import/export and more.
            # "admin" is the only value here that claims admin AUTHORITY
            # (the other values -- architect, business_architect, ... -- are
            # enterprise_role personas, a separate, broader vocabulary this
            # fix does not touch); re-derive it from the same active-org
            # predicate admin_required/org_admin_required already use, rather
            # than trust whatever the paths above contributed.
            if "admin" in user_roles:
                from app.middleware.tenant_decorators import is_platform_admin
                from app.services.rbac_service import rbac_service

                active_org_id = getattr(g, "current_org_id", None)
                if not (
                    is_platform_admin(current_user)
                    or rbac_service.is_org_admin(current_user, active_org_id)
                ):
                    user_roles.discard("admin")

            # Check if user has any of the required roles (case-insensitive)
            required = set(
                normalized
                for normalized in (_normalize_role_name(r) for r in allowed_roles)
                if normalized
            )
            
            if not user_roles & required:  # No intersection = no common roles
                current_app.logger.warning(
                    f"Access denied to {f.__name__}: user {getattr(current_user, 'email', None) or current_user.id} "
                    f"has roles {user_roles}, required {required}"
                )
                abort(403)
            
            return f(*args, **kwargs)
        
        return decorated_function
    
    return decorator
