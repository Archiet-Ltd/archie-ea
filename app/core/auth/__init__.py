"""
Core auth package.

Provides unified authentication and authorization decorators, consolidating
scattered implementations from:
- app/decorators.py (permission_required, admin_required, require_auth, require_feature, require_roles, audit_log)
- app/auth/decorators.py (login_required JSON wrapper, requires_permission)
- app/utils/decorators.py (admin_required duplicate)

Usage::

    from app.core.auth import require_roles, requires_permission
    from app.core.auth.decorators import login_required, require_feature

D-5 (admin-rbac-active-org continuation): ``admin_required`` is no longer
re-exported here -- this package's own copy was one of three separate
implementations and never carried the active-org fix. Import the
canonical, fixed implementation from ``app.decorators`` instead.
"""

from .decorators import (
    audit_log,
    login_required,
    permission_required,
    require_auth,
    require_feature,
    require_roles,
    requires_permission,
)

__all__ = [
    "login_required",
    "requires_permission",
    "permission_required",
    "require_auth",
    "require_feature",
    "require_roles",
    "audit_log",
]
