"""
Admin User Service - Business logic for admin user management.

Extracted from: app/admin/views.py (user CRUD, invitations, role changes)
"""
import logging
from typing import Optional, Tuple

from flask import g
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import Role, User

try:
    from flask_rq import get_queue

    HAS_RQ = True
except ImportError:
    HAS_RQ = False
    get_queue = None

logger = logging.getLogger(__name__)


class AdminUserService:
    """Centralized business logic for admin user management."""

    @staticmethod
    def queue_email(*args, **kwargs) -> None:
        """Send email via queue if available, otherwise send synchronously."""
        from app.flask_email import send_email

        if HAS_RQ and get_queue:
            get_queue().enqueue(send_email, *args, **kwargs)
        else:
            send_email(*args, **kwargs)

    @staticmethod
    def get_all_users():
        """Get all registered users ordered by last name, first name.

        Callers run under admin_required (org-level admin), not
        platform_admin_required, so this is restricted to the current org
        (tenant-scoping-ok: cross-org user-listing IDOR fix).
        """
        return (
            User.query.filter_by(organization_id=g.current_org_id)
            .options(joinedload(User.role))
            .order_by(
                func.lower(func.coalesce(User.last_name, "")),
                func.lower(func.coalesce(User.first_name, "")),
                User.id,
            )
            .all()
        )

    @staticmethod
    def get_all_roles():
        """Get all roles."""
        return Role.query.order_by(func.coalesce(Role.permissions, 0), Role.id).all()

    @staticmethod
    def get_user_or_404(user_id: int) -> User:
        """Get user by ID or abort with 404."""
        from flask import abort

        # tenant-scoping-ok: org-scoped admin, restrict lookup to the current org.
        user = User.query.options(joinedload(User.role)).filter_by(
            id=user_id, organization_id=g.current_org_id
        ).first()
        if user is None:
            abort(404)
        return user

    @staticmethod
    def get_paginated_users(page: int = 1, per_page: int = 10, search_query: str = ""):
        """Get paginated users with optional search.

        Args:
            page: Page number.
            per_page: Items per page.
            search_query: Optional search string for name/email.

        Returns:
            Pagination object.
        """
        # tenant-scoping-ok: org-scoped admin, restrict listing to the current org.
        query = User.query.filter_by(organization_id=g.current_org_id).options(joinedload(User.role))
        if search_query:
            query = query.filter(
                User.first_name.ilike(f"%{search_query}%")
                | User.last_name.ilike(f"%{search_query}%")
                | User.email.ilike(f"%{search_query}%")
            )
        query = query.order_by(User.id.desc())
        return query.paginate(page=page, per_page=per_page, error_out=False)

    @staticmethod
    def create_user(first_name: str, last_name: str, email: str,
                    password: str, role: Role,
                    organization_id: Optional[int] = None) -> User:
        """Create a new user with a password.

        Args:
            first_name: User's first name.
            last_name: User's last name.
            email: User's email.
            password: Plain-text password.
            role: Role to assign.

        Returns:
            The newly created User.

        When ``role`` is Administrator, this also writes the OrgRole row and
        the denormalised ``is_org_admin`` column for the user's organisation
        (the same organisation ``organization_id`` places them in), so the
        team page and database-level guards agree with this page about who is
        an organisation administrator from the moment the account exists --
        through the one shared helper (app/models/org_role.py), not a second
        implementation of the grant.
        """
        from app.models.org_role import apply_admin_role_grant_for_new_user

        user = User(
            first_name=first_name,
            last_name=last_name,
            email=email,
            password=password,
            confirmed=True,
            role=role,
            # The admin's organisation; None falls back to the default org.
            organization_id=organization_id,
        )
        db.session.add(user)
        db.session.flush()
        apply_admin_role_grant_for_new_user(user, role)
        db.session.commit()
        return user

    @staticmethod
    def invite_user(first_name: str, last_name: str, email: str,
                    role: Role, organization_id: Optional[int] = None):
        """Invite a new person into an organisation, by default the signed-in administrator's.

        Goes through the same invitation as the Team page: the account is
        opened in the inviter's organisation and a single-use link is e-mailed.

        Returns ``(user, delivered, error)``. Raises ``InvitationError`` when
        no invitation can be made (for example, e-mail is not available), and
        ``PlanLimitReached`` when the organisation's plan has no place left.
        """
        from flask_login import current_user

        from app.modules.account.services import invitation_service

        if organization_id is None:
            organization_id = current_user.organization_id
        row, delivered, error = invitation_service.invite_new_person(
            organization_id, current_user, email,
            org_role="viewer", first_name=first_name, last_name=last_name,
            platform_role=role,
        )
        return row.user, delivered, error

    @staticmethod
    def change_user_email(user: User, new_email: str) -> None:
        """Change a user's email address (admin action, no confirmation needed).

        Args:
            user: User to modify.
            new_email: New email address.
        """
        user.email = new_email
        db.session.add(user)
        db.session.commit()

    @staticmethod
    def change_user_role(user: User, new_role: Role) -> None:
        """Change a user's role.

        Args:
            user: User to modify.
            new_role: New Role to assign.

        Crossing the Administrator boundary (either direction) also syncs the
        per-organisation OrgRole row and the denormalised ``is_org_admin``
        column, and -- on revoke -- leaves a platform admin's
        Permission.ADMINISTER untouched, through the one shared helper
        (app/models/org_role.py) that both the v1 and v2 admin services call,
        instead of this service re-implementing the state transition (which
        previously reassigned ``user.role`` unconditionally, stripping a
        platform admin's Administrator role as a side effect).
        """
        from app.models.org_role import apply_admin_role_change

        apply_admin_role_change(user, new_role)
        db.session.add(user)
        db.session.commit()

    @staticmethod
    def set_user_password(user: User, new_password: str, confirm_user: bool = True) -> None:
        """Set a user's password and optionally mark the account confirmed.

        Revokes every existing session for the user (D2, round 2): this is
        the primary incident-response path -- an admin resetting a
        compromised account's password must also kick out whatever session
        the attacker's captured cookie is still riding on, or the reset is
        cosmetic.
        """
        user.password = new_password
        if confirm_user:
            user.confirmed = True
        db.session.add(user)
        db.session.commit()

        from app.services import session_registry

        try:
            session_registry.revoke_all_for_user(user.id, "admin")
        except Exception:
            logging.getLogger(__name__).error(
                "set_user_password: failed to revoke sessions for user_id=%s", user.id, exc_info=True
            )

    @staticmethod
    def delete_user(user: User) -> Tuple[bool, str]:
        """Delete a user.

        Args:
            user: User to delete.

        Returns:
            Tuple of (success, message).
        """
        user_name = user.full_name()
        db.session.delete(user)
        db.session.commit()
        return True, "Successfully deleted user {}.".format(
            user_name
        )
