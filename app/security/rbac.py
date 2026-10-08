"""
Fine-grained Role-Based Access Control (RBAC) System

Extends the basic role system with resource-specific permissions and domain-based access control.
Provides enterprise-grade authorization for multi-tenant architecture platform.

Key Features:
- Resource-level permissions (read, write, delete, admin)
- Domain-based access control (architecture, applications, vendors, etc.)
- Role inheritance and permission aggregation
- Context-aware authorization checks
- Audit trail integration
"""

import logging
from enum import Enum
from typing import Dict, List, Optional

from flask_login import current_user

from app.models.user import Role, User

logger = logging.getLogger(__name__)


class Permission(Enum):
    """Permission levels for resources"""

    NONE = 0
    READ = 1
    WRITE = 2
    DELETE = 4
    ADMIN = 8

    @classmethod
    def all(cls) -> int:
        """Get combined value of all permissions"""
        return cls.READ.value | cls.WRITE.value | cls.DELETE.value | cls.ADMIN.value


class ResourceDomain(Enum):
    """Resource domains for access control"""

    ARCHITECTURE = "architecture"
    APPLICATIONS = "applications"
    VENDORS = "vendors"
    CAPABILITIES = "capabilities"
    ROADMAP = "roadmap"
    COMPLIANCE = "compliance"
    ADMIN = "admin"
    AUDIT = "audit"
    SECURITY = "security"


class RBACManager:
    """
    Centralized RBAC authorization manager.

    Handles permission checks, role assignments, and access control decisions.
    """

    def __init__(self):
        self._permission_cache: Dict[str, int] = {}
        self._role_cache: Dict[int, Dict[str, int]] = {}

    def check_permission(
        self,
        user: User,
        resource_domain: ResourceDomain,
        permission: Permission,
        resource_id: Optional[str] = None,
    ) -> bool:
        """
        Check if user has specific permission for a resource domain.

        Args:
            user: User to check permissions for
            resource_domain: Domain of the resource
            permission: Required permission level
            resource_id: Optional specific resource identifier

        Returns:
            True if user has permission, False otherwise
        """
        if not user or not user.is_authenticated:
            return False

        # D-4 (admin-rbac-active-org continuation): this used to be
        # ``user.is_admin()`` -- a global ``Permission.ADMINISTER`` flag,
        # independent of which organisation is active in the session
        # (``g.current_org_id``). Since every self-registered user is
        # Administrator of their own organisation, a user who merely
        # accepted a Viewer invitation into another organisation and
        # switched their session into it was granted every permission on
        # every resource domain there too (architecture, applications,
        # vendors, capabilities, roadmap, compliance, ...) -- the exact bug
        # ``admin_required``/``org_admin_required`` already fix elsewhere in
        # this PR. Admin users in the ACTIVE organisation still have all
        # permissions. _get_role_permissions' own, separate "Administrator
        # role -> Permission.all()" escalation is removed below rather than
        # also made active-org-aware in place, so this is the one and only
        # place that grants it -- threading g.current_org_id through
        # _get_user_permissions' cache (keyed only by user id and domain,
        # not by organisation) would silently serve one organisation's
        # cached admin-level permissions to another.
        from app.middleware.tenant_decorators import is_platform_admin
        from app.services.rbac_service import rbac_service
        from flask import g

        active_org_id = getattr(g, "current_org_id", None)
        if is_platform_admin(user) or rbac_service.is_org_admin(user, active_org_id):
            return True

        # Get user's effective permissions for this domain
        user_permissions = self._get_user_permissions(user, resource_domain)

        # Check if required permission is granted
        return (user_permissions & permission.value) == permission.value

    def check_any_permission(
        self, user: User, resource_domain: ResourceDomain, permissions: List[Permission]
    ) -> bool:
        """
        Check if user has any of the specified permissions.

        Args:
            user: User to check
            resource_domain: Resource domain
            permissions: List of permissions to check

        Returns:
            True if user has at least one permission
        """
        return any(self.check_permission(user, resource_domain, perm) for perm in permissions)

    def require_permission(
        self,
        resource_domain: ResourceDomain,
        permission: Permission,
        resource_id: Optional[str] = None,
    ):
        """
        Flask decorator to require specific permission.

        Usage:
            @app.route('/api/architecture')
            @rbac.require_permission(ResourceDomain.ARCHITECTURE, Permission.READ)
            def get_architecture():
                pass
        """

        def decorator(f):
            def wrapper(*args, **kwargs):
                if not current_user or not current_user.is_authenticated:
                    from flask import abort

                    abort(401, "Authentication required")

                if not self.check_permission(
                    current_user, resource_domain, permission, resource_id
                ):
                    logger.warning(
                        f"Access denied for user {current_user.id} to {resource_domain.value}:{resource_id}"
                    )
                    from flask import abort

                    abort(403, "Insufficient permissions")

                return f(*args, **kwargs)

            wrapper.__name__ = f.__name__
            return wrapper

        return decorator

    def _get_user_permissions(self, user: User, resource_domain: ResourceDomain) -> int:
        """
        Get effective permissions for user in a specific domain.

        Includes role-based permissions and any user-specific overrides.
        """
        cache_key = f"{user.id}:{resource_domain.value}"

        if cache_key in self._permission_cache:
            return self._permission_cache[cache_key]

        permissions = 0

        # Get permissions from user's role
        if user.role:
            role_perms = self._get_role_permissions(user.role, resource_domain)
            permissions |= role_perms

        # User-specific permission overrides not yet required
        # permissions |= self._get_user_specific_permissions(user, resource_domain)

        # Cache the result
        self._permission_cache[cache_key] = permissions

        return permissions

    def _get_role_permissions(self, role: Role, resource_domain: ResourceDomain) -> int:
        """
        Get permissions for a role in a specific domain, for a user who is
        NOT an admin of the organisation currently active in their session
        (``check_permission`` above already returns early -- with all
        permissions -- for one who is).

        D-4 (admin-rbac-active-org continuation): this used to ALSO grant
        every permission (``Permission.all()``) whenever ``role.permissions
        == Permission.ADMINISTER`` or ``role.name == "Administrator"`` --
        the same global, not-active-org-scoped flag ``check_permission``'s
        own bypass used, reachable independently of it (a caller that
        reaches this method at all has already been refused that bypass).
        Removed rather than re-derived here: ``_get_user_permissions``'
        cache is keyed only by user id and domain, not by organisation, so
        threading ``g.current_org_id`` through to this layer would risk
        serving one organisation's cached admin-level permissions to
        another the next time the same user is checked in a different one.
        """
        # Domain-specific permission mapping (non-admin baseline only).
        domain_permissions = {
            ResourceDomain.ARCHITECTURE: Permission.READ.value | Permission.WRITE.value,
            ResourceDomain.APPLICATIONS: Permission.READ.value | Permission.WRITE.value,
            ResourceDomain.VENDORS: Permission.READ.value | Permission.WRITE.value,
            ResourceDomain.CAPABILITIES: Permission.READ.value | Permission.WRITE.value,
            ResourceDomain.ROADMAP: Permission.READ.value | Permission.WRITE.value,
            ResourceDomain.COMPLIANCE: Permission.READ.value,
            ResourceDomain.ADMIN: Permission.NONE.value,
            ResourceDomain.AUDIT: Permission.NONE.value,
            ResourceDomain.SECURITY: Permission.NONE.value,
        }

        return domain_permissions.get(resource_domain, Permission.NONE.value)

    def get_user_domains(self, user: User) -> List[ResourceDomain]:
        """
        Get list of domains user has access to.

        Returns:
            List of ResourceDomain enums user can access
        """
        accessible_domains = []

        for domain in ResourceDomain:
            if self.check_permission(user, domain, Permission.READ):
                accessible_domains.append(domain)

        return accessible_domains

    def clear_cache(self, user_id: Optional[int] = None):
        """
        Clear permission cache.

        Args:
            user_id: Optional user ID to clear cache for, or all users if None
        """
        if user_id:
            # Clear cache for specific user
            keys_to_remove = [
                k for k in self._permission_cache.keys() if k.startswith(f"{user_id}:")
            ]
            for key in keys_to_remove:
                del self._permission_cache[key]
        else:
            # Clear all cache
            self._permission_cache.clear()
            self._role_cache.clear()


# Global RBAC manager instance
rbac_manager = RBACManager()


def check_permission(
    resource_domain: ResourceDomain, permission: Permission, resource_id: Optional[str] = None
):
    """
    Convenience function to check current user's permission.

    Returns:
        True if current user has permission, False otherwise
    """
    if not current_user or not current_user.is_authenticated:
        return False

    return rbac_manager.check_permission(current_user, resource_domain, permission, resource_id)


def require_permission(
    resource_domain: ResourceDomain, permission: Permission, resource_id: Optional[str] = None
):
    """
    Decorator to require permission for Flask routes.

    Usage:
        @app.route('/api/architecture')
        @require_permission(ResourceDomain.ARCHITECTURE, Permission.READ)
        def get_architecture():
            pass
    """

    def decorator(f):
        def wrapper(*args, **kwargs):
            if not current_user or not current_user.is_authenticated:
                from flask import abort

                abort(401, "Authentication required")

            if not rbac_manager.check_permission(
                current_user, resource_domain, permission, resource_id
            ):
                logger.warning(
                    f"Access denied for user {current_user.id} to {resource_domain.value}:{resource_id}"
                )
                from flask import abort

                abort(403, "Insufficient permissions")

            return f(*args, **kwargs)

        wrapper.__name__ = f.__name__
        return wrapper

    return decorator
