"""
AuthService — thin facade for common authentication operations.

Provides a service-layer abstraction over Flask-Login and the User model
so that modules don't need to reach into the ORM directly for auth tasks.

Usage::

    from app.core.auth.service import AuthService

    svc = AuthService()
    user = svc.get_current_user()
    if svc.user_has_role(user, "admin"):
        ...
"""

import logging
from typing import List

from flask_login import current_user

logger = logging.getLogger(__name__)


class AuthService:
    """Stateless service for authentication queries."""

    @staticmethod
    def get_current_user():
        """Return the currently logged-in user (or anonymous proxy)."""
        return current_user

    @staticmethod
    def is_authenticated() -> bool:
        """Check if the current request has an authenticated user."""
        return current_user.is_authenticated

    @staticmethod
    def user_has_role(user, role_name: str) -> bool:
        """Check if *user* has the given role (case-insensitive).

        Handles multiple user-model formats:
        - ``user.roles`` (list of role objects or strings)
        - ``user.role_names`` (list of strings)
        - ``user.role`` (single string)
        """
        target = role_name.lower()

        if hasattr(user, "roles"):
            for role in user.roles:
                name = getattr(role, "name", role) if not isinstance(role, str) else role
                if str(name).lower() == target:
                    return True

        if hasattr(user, "role_names"):
            if target in {r.lower() for r in user.role_names}:
                return True

        if hasattr(user, "role"):
            if str(user.role).lower() == target:
                return True

        return False

    @staticmethod
    def user_has_any_role(user, role_names: List[str]) -> bool:
        """Check if *user* has at least one of the given roles."""
        return any(AuthService.user_has_role(user, r) for r in role_names)

    @staticmethod
    def is_admin(user=None) -> bool:
        """Check if *user* (default: current_user) is an admin of the
        ACTIVE organisation.

        R3-1 (PR 428 round 4): ``getattr(user, "is_admin", False)`` with no
        call returns the bound method, always truthy for every signed-in
        user -- the same bug class this round fixes elsewhere. No current
        caller uses this method (confirmed: grepped the whole tree), but a
        static source check cannot distinguish dead code from the next
        caller, so it is fixed the same way rather than left as a landmine.
        ``is_superuser`` does not exist on ``User`` at all.
        """
        from app.middleware.tenant_decorators import is_active_org_admin

        user = user or current_user
        return is_active_org_admin(user) or AuthService.user_has_role(user, "admin")
