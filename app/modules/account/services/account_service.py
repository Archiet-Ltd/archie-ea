"""
Account Service -- authentication and account management business logic.

Migrated from: app/account/views.py (inline logic extracted to service layer)
All behavior preserved exactly from the original views.py implementation.
"""
import logging

from flask import g, session, url_for
from flask_login import logout_user

try:
    from flask_rq import get_queue

    HAS_RQ = True
except ImportError:
    HAS_RQ = False
    get_queue = None

from app.extensions import db
from app.flask_email import send_email
from app.models import User
from app.models.org_role import OrgRole
from app.services import session_registry

_log = logging.getLogger(__name__)


def _queue_email(*args, **kwargs):
    """Send email via queue if available, otherwise send synchronously."""
    if HAS_RQ and get_queue:
        get_queue().enqueue(send_email, *args, **kwargs)
    else:
        send_email(*args, **kwargs)


class AccountService:
    """Service layer for account-related operations."""

    @staticmethod
    def switch_active_organization(user, requested_org_id):
        """Switch the signed-in user's active organisation.

        Returns ``(success, message)`` so both account blueprints can keep the
        same flash/redirect behaviour while delegating the membership and
        session handling to one implementation.
        """
        from app.middleware.tenant_context import (
            ACTIVE_ORG_SESSION_KEY,
            accessible_organizations,
            clear_tenant_context_cache,
            user_can_access_org,
        )
        from app.models.organization import Organization

        memberships = accessible_organizations(user)
        if requested_org_id is None:
            return False, "Select an organisation to continue."

        if not any(org.id == requested_org_id for org in memberships) or not user_can_access_org(
            user, requested_org_id
        ):
            session.pop(ACTIVE_ORG_SESSION_KEY, None)
            session.modified = True
            clear_tenant_context_cache()
            return False, "You do not have access to that organisation."

        session[ACTIVE_ORG_SESSION_KEY] = requested_org_id
        session.modified = True
        clear_tenant_context_cache()

        active_org = db.session.get(Organization, requested_org_id)
        g.current_org_id = requested_org_id
        g.current_org = active_org
        return True, (
            f"Now working in {active_org.name if active_org else 'the selected organisation'}."
        )

    @staticmethod
    def authenticate(email, password):
        """Authenticate a user by email and password.

        Returns the User object if credentials are valid, None otherwise.
        """
        user = User.find_by_email(email)
        if (
            user is not None
            and user.password_hash is not None
            and user.verify_password(password)
        ):
            return user
        return None

    @staticmethod
    def login(user, remember_me=False):
        """Log in a user via flask-login and mint a server-side session record."""
        session_registry.login_and_register(user, remember=remember_me)

    @staticmethod
    def logout():
        """Log out the current user, revoking their server-side session record.

        ``logout_user()`` always runs, even if the registry write fails: a DB
        blip revoking the server-side record must not turn a logout into a
        500 with the user still logged in client-side (low-priority item,
        round 2). The failure is still surfaced to the caller so it can be
        logged/reported rather than silently swallowed.
        """
        sid = session.get("_sid")
        try:
            session_registry.revoke(sid, "logout")
        except Exception:
            _log.error("account_service: failed to revoke session sid on logout", exc_info=True)
        finally:
            logout_user()

    @staticmethod
    def register_user(first_name, last_name, email, password, confirmed=True):
        """Register a new user in an organisation of their own, and sign them in.

        ``confirmed`` is False when the address still has to be confirmed by
        e-mail; ``sign_up`` below decides that from whether mail can be sent.

        Returns the newly created User object.
        """
        # Each self-serve sign-up gets its OWN organization (single-tenant isolation).
        # Team members are invited into an existing org rather than registering here.
        import re as _re
        import uuid as _uuid
        from app.models import Organization
        from app.modules.account.services.invitation_service import release_for_registration

        # An address held only by an invitation nobody took up is freed here.
        release_for_registration(email)
        _label = ("{} {}".format(first_name or "", last_name or "").strip()
                  or (email.split("@")[0] if email else "New"))
        _base_slug = _re.sub(r"[^a-z0-9]+", "-",
                             ((first_name or "") + (last_name or "") or email.split("@")[0]).lower()).strip("-") or "org"
        _slug = _base_slug
        while Organization.query.filter_by(slug=_slug).first():
            _slug = "{}-{}".format(_base_slug, _uuid.uuid4().hex[:6])
        org = Organization(name="{}'s Workspace".format(_label), slug=_slug)
        db.session.add(org)
        db.session.flush()

        user = User(
            first_name=first_name,
            last_name=last_name,
            email=email,
            password=password,
            confirmed=confirmed,
            organization_id=org.id,
            # Self-serve sign-ups start on the small, plain-language owner view.
            # Authority is unchanged: grant_org_admin() below is what carries it.
            enterprise_role="business_owner",
        )
        # The user owns the organisation just created for them, so granting
        # org-admin here is always a grant in their own organisation.
        user.grant_org_admin()
        db.session.add(user)
        try:
            db.session.flush()
            OrgRole.set_role(org.id, user.id, "org_admin", granted_by_id=user.id)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        # Auto-login after registration
        session_registry.login_and_register(user)
        return user

    @staticmethod
    def sign_up(first_name, last_name, email, password):
        """Self-serve registration, with the address confirmed by e-mail.

        Returns ``(user, confirmation)``: "sent" (a link was mailed and the
        account waits on it), "failed" (the mail server refused it; the
        account waits and can ask for another), or "mail_unavailable" (no
        mail server here, so the account is usable at once and the person is
        told no message was sent -- holding it behind a message that can
        never arrive would lock them out of their own trial).
        """
        from app.flask_email import mail_available

        can_mail = mail_available()
        user = AccountService.register_user(
            first_name, last_name, email, password, confirmed=not can_mail
        )
        if not can_mail:
            return user, "mail_unavailable"
        delivered, _error = AccountService.send_confirmation_email(user)
        return user, ("sent" if delivered else "failed")

    @staticmethod
    def request_password_reset(email):
        """Mail a single-use reset link if an account uses ``email``.

        Returns "mail_unavailable" when this server cannot send mail (decided
        before the address is looked up, so the answer is the same for every
        address), otherwise "requested" whether or not the address exists.
        """
        from app.flask_email import deliver_email_after_response, mail_available
        from app.models.account_token import PURPOSE_PASSWORD_RESET, AccountToken

        if not mail_available():
            return "mail_unavailable"
        user = User.find_by_email(email)
        if user is not None and user.password_hash is not None:
            row, raw = AccountToken.issue(user, PURPOSE_PASSWORD_RESET)
            db.session.commit()
            token_id = row.id

            def record(delivered, error):
                # tenant-scoping-ok: the row this request just issued, by primary key
                issued = db.session.get(AccountToken, token_id)
                if issued is not None:
                    issued.record_delivery(delivered, error)
                    db.session.commit()

            # Sent after the answer has gone out, so the answer takes as long
            # for an address with an account as for one without.
            deliver_email_after_response(
                recipient=user.email,
                subject="Reset your password",
                template="account/email/reset_password",
                user=user,
                reset_link=url_for("account.reset_password", token=raw, _external=True),
                expires_at=row.expires_at,
                on_result=record,
            )
        return "requested"

    @staticmethod
    def reset_link_usable(token):
        from app.models.account_token import PURPOSE_PASSWORD_RESET, AccountToken

        return AccountToken.find_usable(token, PURPOSE_PASSWORD_RESET) is not None

    @staticmethod
    def reset_password(token, new_password):
        """Set a new password through a reset link. The link works once.

        Returns (success: bool, message: str). No session is created.
        """
        from app.models.account_token import PURPOSE_PASSWORD_RESET, AccountToken

        row = AccountToken.consume(token, PURPOSE_PASSWORD_RESET)
        if row is None:
            return False, "This reset link has expired or has already been used."
        user = row.user
        user.password = new_password
        db.session.add(user)
        db.session.commit()
        # I've-lost-control-of-this-account path: kill everything,
        # including any session on the machine performing the reset.
        # There is no acting session to preserve -- a reset happens
        # while logged out.
        AccountService._revoke_other_sessions(user.id, "password_change", except_sid=None)
        return True, "Your password has been updated. Sign in with your new password."

    @staticmethod
    def change_password(user, old_password, new_password):
        """Change a user's password after verifying the old one.

        Returns (success: bool, message: str, revoked_count: int | None).
        ``revoked_count`` is None when the password change itself failed, or
        when the change succeeded but session revocation could not be
        confirmed (caller must not report a fabricated count in that case).
        """
        if user.verify_password(old_password):
            user.password = new_password
            db.session.add(user)
            db.session.commit()
            # Keep the device the user is changing the password from signed
            # in -- only terminate every OTHER active session.
            revoked = AccountService._revoke_other_sessions(
                user.id, "password_change", except_sid=session.get("_sid")
            )
            return True, "Your password has been updated.", revoked
        return False, "Original password is invalid.", None

    @staticmethod
    def _revoke_other_sessions(user_id, reason, except_sid):
        """Revoke other active sessions for ``user_id``.

        Returns the number revoked, or ``None`` if revocation itself failed
        -- the password change has already committed at this point, so a
        revocation failure must never abort the request; it must also never
        be reported to the user as a specific count it cannot back up
        (fabricated-data rule).
        """
        try:
            count = session_registry.revoke_all_for_user(user_id, reason, except_sid=except_sid)
            # Audited unconditionally, including count == 0 (D5, round 2):
            # "password changed, zero other sessions to revoke" is itself
            # evidence a revocation check ran, and skipping the audit row
            # when count is 0 left no trace that it ever happened.
            from app.services import auth_audit

            user = User.query.get(user_id)  # tenant-scoping-ok: own-account post-auth lookup by primary key
            auth_audit.record_sessions_revoked(user, reason, count)
            return count
        except Exception:
            _log.error(
                "account_service: failed to revoke other sessions for user_id=%s reason=%s",
                user_id, reason, exc_info=True,
            )
            return None

    @staticmethod
    def request_email_change(user, new_email, password):
        """Request an email change after verifying the password.

        Returns (success: bool, message: str).
        """
        if user.verify_password(password):
            token = user.generate_email_change_token(new_email)
            change_email_link = url_for("account.change_email", token=token, _external=True)
            _queue_email(
                recipient=new_email,
                subject="Confirm Your New Email",
                template="account/email/change_email",
                user=user._get_current_object() if hasattr(user, '_get_current_object') else user,
                change_email_link=change_email_link,
            )
            return True, "A confirmation link has been sent to {}.".format(new_email)
        return False, "Invalid email or password."

    @staticmethod
    def confirm_email_change(user, token):
        """Confirm email change with the given token.

        Returns (success: bool, message: str).
        """
        if user.change_email(token):
            return True, "Your email address has been updated."
        return False, "The confirmation link is invalid or has expired."

    @staticmethod
    def send_confirmation_email(user):
        """Mail (or re-mail) a single-use link confirming the user's address.

        Returns ``(delivered, error)``; ``error`` says why nothing went out.
        """
        from app.flask_email import deliver_email, mail_available
        from app.models.account_token import PURPOSE_CONFIRM_EMAIL, AccountToken

        actual_user = user._get_current_object() if hasattr(user, '_get_current_object') else user
        if not mail_available():
            return False, "E-mail is not available on this server."
        row, raw = AccountToken.issue(actual_user, PURPOSE_CONFIRM_EMAIL)
        confirm_link = url_for("account.confirm", token=raw, _external=True)
        delivered, error = deliver_email(
            recipient=actual_user.email,
            subject="Confirm your account",
            template="account/email/confirm",
            user=actual_user,
            confirm_link=confirm_link,
            expires_at=row.expires_at,
        )
        row.record_delivery(delivered, error)
        db.session.commit()
        return delivered, error

    @staticmethod
    def confirm_account(token):
        """Confirm the address behind a confirmation link. The link works once.

        Returns (user or None, message).
        """
        from app.models.account_token import PURPOSE_CONFIRM_EMAIL, AccountToken

        row = AccountToken.consume(token, PURPOSE_CONFIRM_EMAIL)
        if row is None:
            db.session.rollback()
            return None, "This confirmation link has expired or has already been used."
        row.user.confirmed = True
        db.session.commit()
        return row.user, "Your e-mail address is confirmed."

    @staticmethod
    def accept_invitation(user, invitation_id):
        """Accept a pending invitation for the current user.

        Returns (success: bool, message: str). On success, creates the
        OrgRole row, removes the pending invitation, and — the same as the
        ``/account/join/<token>`` path (``invitation_service.answer_existing``)
        — syncs the one canonical admin authority when the invitation is for
        the user's own organisation, so this route never disagrees with the
        toggle/team/invitation-link paths about who is an organisation
        administrator.
        """
        from app.models.pending_invitation import PendingInvitation

        # tenant-scoping-ok: gated by user_id check below — only the
        # invitation owner can accept their own invitations.
        invitation = db.session.get(PendingInvitation, invitation_id)
        if invitation is None:
            return False, "Invitation not found."
        if invitation.user_id != user.id:
            return False, "This invitation is not for you."
        if invitation.is_expired():
            db.session.delete(invitation)
            db.session.commit()
            return False, "This invitation has expired. Ask an administrator to invite you again."
        OrgRole.set_role(
            invitation.organization_id,
            user.id,
            invitation.role,
            granted_by_id=invitation.invited_by,
        )
        # The Administrator role is global to the user, not scoped to one
        # organisation: only touch it when the invitation is for the user's
        # OWN organisation, exactly as answer_existing does, so accepting an
        # invitation into a different organisation can never grant or revoke
        # admin in the user's own one.
        if invitation.organization_id == user.organization_id:
            if invitation.role == "org_admin":
                user.grant_org_admin()
            elif user.is_admin():
                user.revoke_org_admin()
        db.session.delete(invitation)
        db.session.commit()
        return True, "Invitation accepted."

    @staticmethod
    def decline_invitation(user, invitation_id):
        """Decline a pending invitation for the current user.

        Returns (success: bool, message: str). On success, removes the
        pending invitation without granting any role.
        """
        from app.models.pending_invitation import PendingInvitation

        # tenant-scoping-ok: gated by user_id check below — only the
        # invitation owner can decline their own invitations.
        invitation = db.session.get(PendingInvitation, invitation_id)
        if invitation is None:
            return False, "Invitation not found."
        if invitation.user_id != user.id:
            return False, "This invitation is not for you."
        db.session.delete(invitation)
        db.session.commit()
        return True, "Invitation declined."
