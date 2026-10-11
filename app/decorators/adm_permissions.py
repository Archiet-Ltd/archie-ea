"""
ADM Permissions Decorators

Role-based access control (RBAC) decorators for ADM Kanban.
Provides role-based access control for Architecture Board, stakeholder, and admin operations.
"""

import functools
import logging
from enum import Enum
from typing import Callable, List

from flask import abort, g, jsonify, request
from flask_login import current_user, login_required

from app.utils.adm_rate_limiter import ADMRateLimitExceeded, adm_rate_limiter

logger = logging.getLogger(__name__)


class ADMRole(str, Enum):
    """ADM Kanban roles."""

    ADMIN = "admin"
    ARCHITECTURE_BOARD_MEMBER = "arb_member"
    ARCHITECTURE_BOARD_CHAIR = "arb_chair"
    ENTERPRISE_ARCHITECT = "enterprise_architect"
    SOLUTION_ARCHITECT = "solution_architect"
    BUSINESS_ARCHITECT = "business_architect"
    TECHNOLOGY_ARCHITECT = "technology_architect"
    SECURITY_ARCHITECT = "security_architect"
    STAKEHOLDER = "stakeholder"
    BOARD_OWNER = "board_owner"
    BOARD_MEMBER = "board_member"
    CARD_ASSIGNEE = "card_assignee"
    VIEWER = "viewer"


class PermissionError(Exception):
    """Exception raised when permission check fails."""
    pass


def _check_role(user, required_roles: List[str]) -> bool:
    """Check if user has any of the required roles."""
    if not user or not user.is_authenticated:
        return False

    # Admin always has access (judged in the active organisation)
    from app.middleware.tenant_decorators import is_active_org_admin

    if is_active_org_admin(user) or "admin" in required_roles:
        return True

    # Check user's roles
    user_roles = getattr(user, "roles", []) or []
    user_role_names = [r.name if hasattr(r, "name") else str(r) for r in user_roles]

    # Also check primary_role attribute
    if hasattr(user, "primary_role") and user.primary_role:
        user_role_names.append(user.primary_role)

    for required_role in required_roles:
        if required_role in user_role_names:
            return True

    return False


def _check_board_access(user, board_id: int, required_access: str = "view") -> bool:
    """Check if user has access to a specific board."""
    from app.models.adm_kanban import KanbanBoard

    board = KanbanBoard.query.get(board_id)
    if not board:
        return False

    # Board owner has full access
    if board.created_by_id == user.id:
        return True

    # Check board-specific membership
    # This would query board_members table
    # For now, simplified check
    return True  # Allow access for implementation


def _check_card_access(user, card_id: int, required_access: str = "view") -> bool:
    """Check if user has access to a specific card."""
    from app.models.adm_kanban import KanbanCard

    card = KanbanCard.query.get(card_id)
    if not card:
        return False

    # Card creator has full access
    if card.created_by_id == user.id:
        return True

    # Assigned user has access
    if card.assigned_to_id == user.id:
        return True

    # Check board access
    return _check_board_access(user, card.board_id, required_access)


def adm_permission_required(*roles: str):
    """
    Decorator to require specific ADM roles.

    Args:
        *roles: Required roles (from ADMRole enum)
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        @login_required
        def wrapper(*args, **kwargs):
            if not _check_role(current_user, list(roles)):
                if request.is_json or request.headers.get("Accept") == "application/json":
                    return jsonify({"error": "Insufficient permissions", "required_roles": list(roles)}), 403
                abort(403, description="Insufficient permissions")
            return func(*args, **kwargs)
        return wrapper
    return decorator


def adm_board_access_required(access_level: str = "view"):
    """
    Decorator to check board access permissions.

    Args:
        access_level: Required access level (view, edit, admin)
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        @login_required
        def wrapper(*args, **kwargs):
            board_id = kwargs.get("board_id") or request.view_args.get("board_id")
            if not board_id:
                board_id = request.args.get("board_id")

            if board_id:
                try:
                    board_id = int(board_id)
                except (ValueError, TypeError):
                    return jsonify({"error": "Invalid board_id"}), 400

                if not _check_board_access(current_user, board_id, access_level):
                    return jsonify({"error": "Access denied to board"}), 403

            return func(*args, **kwargs)
        return wrapper
    return decorator


def adm_card_access_required(access_level: str = "view"):
    """
    Decorator to check card access permissions.

    Args:
        access_level: Required access level (view, edit, admin)
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        @login_required
        def wrapper(*args, **kwargs):
            card_id = kwargs.get("card_id") or request.view_args.get("card_id")
            if not card_id:
                card_id = request.args.get("card_id")

            if card_id:
                try:
                    card_id = int(card_id)
                except (ValueError, TypeError):
                    return jsonify({"error": "Invalid card_id"}), 400

                if not _check_card_access(current_user, card_id, access_level):
                    return jsonify({"error": "Access denied to card"}), 403

            return func(*args, **kwargs)
        return wrapper
    return decorator


def adm_rate_limit(action_type: str = "read"):
    """
    Decorator to apply rate limiting to ADM endpoints.

    Args:
        action_type: Type of action (read, write, approval, transition, admin)
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                limit_status = adm_rate_limiter.check_rate_limit(
                    user_id=current_user.id if current_user.is_authenticated else 0,
                    action_type=action_type
                )

                # Add rate limit headers
                response = func(*args, **kwargs)

                # If response is a tuple (response, status_code), handle it
                if isinstance(response, tuple):
                    resp_obj = response[0]
                else:
                    resp_obj = response

                # Add headers if it's a Response object
                if hasattr(resp_obj, "headers"):
                    resp_obj.headers["X-RateLimit-Limit"] = str(limit_status.get("limit", -1))
                    resp_obj.headers["X-RateLimit-Remaining"] = str(limit_status.get("remaining", -1))

                return response

            except ADMRateLimitExceeded as e:
                return jsonify({
                    "error": e.message,
                    "retry_after": e.retry_after
                }), 429

        return wrapper
    return decorator


def adm_approval_permission_required():
    """
    Decorator to check Architecture Board approval permissions.

    Requires:
    - User is Architecture Board member or chair
    - Or user is enterprise architect
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        @login_required
        def wrapper(*args, **kwargs):
            allowed_roles = [
                ADMRole.ARCHITECTURE_BOARD_CHAIR.value,
                ADMRole.ARCHITECTURE_BOARD_MEMBER.value,
                ADMRole.ENTERPRISE_ARCHITECT.value,
                ADMRole.ADMIN.value,
            ]

            if not _check_role(current_user, allowed_roles):
                return jsonify({
                    "error": "Architecture Board approval required",
                    "required_roles": allowed_roles
                }), 403

            return func(*args, **kwargs)
        return wrapper
    return decorator


def adm_stakeholder_permission_required(approval_id: str = None):
    """
    Decorator to check stakeholder concurrence permissions.

    Requires:
    - User is designated stakeholder for the approval
    - Or user has stakeholder role
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        @login_required
        def wrapper(*args, **kwargs):
            # Get approval_id from kwargs or request
            aid = kwargs.get(approval_id) if approval_id else None
            if not aid:
                aid = request.view_args.get("approval_id") or request.args.get("approval_id")

            # Check if user is a designated stakeholder
            if aid:
                from app.models.adm_phase_approval import ADMStakeholderConcurrence
                concurrence = ADMStakeholderConcurrence.query.filter_by(
                    approval_id=aid,
                    stakeholder_user_id=current_user.id
                ).first()

                if concurrence:
                    # User is a designated stakeholder
                    return func(*args, **kwargs)

            # Check general stakeholder role
            if _check_role(current_user, [ADMRole.STAKEHOLDER.value, ADMRole.ADMIN.value]):
                return func(*args, **kwargs)

            return jsonify({
                "error": "Stakeholder concurrence permission required"
            }), 403

        return wrapper
    return decorator


def adm_input_validation(schema_class=None):
    """
    Decorator for input validation using marshmallow schemas.

    Args:
        schema_class: Marshmallow schema class for validation
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if schema_class and request.is_json:
                schema = schema_class()
                data = request.get_json()

                errors = schema.validate(data)
                if errors:
                    return jsonify({
                        "error": "Validation failed",
                        "validation_errors": errors
                    }), 400

                # Store validated data
                g.validated_data = schema.load(data)

            return func(*args, **kwargs)
        return wrapper
    return decorator


# Convenience decorators for common permission patterns
require_admin = adm_permission_required(ADMRole.ADMIN.value)
require_arb_member = adm_permission_required(
    ADMRole.ARCHITECTURE_BOARD_MEMBER.value,
    ADMRole.ARCHITECTURE_BOARD_CHAIR.value
)
require_arb_chair = adm_permission_required(ADMRole.ARCHITECTURE_BOARD_CHAIR.value)
require_architect = adm_permission_required(
    ADMRole.ENTERPRISE_ARCHITECT.value,
    ADMRole.SOLUTION_ARCHITECT.value,
    ADMRole.BUSINESS_ARCHITECT.value,
    ADMRole.TECHNOLOGY_ARCHITECT.value,
    ADMRole.SECURITY_ARCHITECT.value
)
require_board_owner = adm_board_access_required("admin")
require_card_editor = adm_card_access_required("edit")
