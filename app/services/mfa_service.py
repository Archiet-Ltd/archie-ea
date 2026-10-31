"""
MFA service — TOTP multi-factor authentication, required for administrators
(R1-B12 PR 2, TB-0144/PB-0100).

One accessor for everything that generates, verifies or checks enrolment of
a time-based one-time-password secret (ADR 0008). No route builds a
``pyotp.TOTP`` directly.

Usage::

    if mfa_service.required_for(user):
        if not user.mfa_enabled:
            secret = mfa_service.generate_secret()
            uri = mfa_service.provisioning_uri(user, secret)
            # show secret/QR, ask for one code, then:
            recovery_codes = mfa_service.enroll(user, secret, code)
        else:
            mfa_service.verify_code(user, code)  # raises MFAError on failure
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


class MFAError(Exception):
    """Raised when an MFA code is missing, wrong, or reused."""


def required_for(user) -> bool:
    """True when ``user`` must complete MFA before a login is allowed to
    finish.

    Administrators must always complete MFA, whether or not they have
    enrolled yet: an unenrolled administrator is sent to enrol, not let
    through. "Administrator" here is any of three things: a platform admin
    (``is_platform_admin``); an admin of the user's own home organisation
    (``user.is_org_admin``, kept here explicitly as defence-in-depth even
    though ``is_org_admin_anywhere`` below also covers the home
    organisation); or an organisation admin of ANY organisation the user
    belongs to -- their own home organisation or one they were invited into
    (``rbac_service.is_org_admin_anywhere``, second refuter pass, R1).

    This does not exempt an administrator of only a deactivated
    organisation: deactivation is not enforced at login or at
    session-switch time, so such an administrator can still sign in and
    switch into it exactly as before, and MFA authority fails closed on
    that fact rather than leaning on enforcement that does not exist
    elsewhere (third refuter pass, R5; see ``rbac_service.org_ids_for``).

    The one exception is ``_admin_mfa_bypass_active()`` below -- see its own
    docstring for exactly what that is and is not."""
    if user is None:
        return False
    from app.services.rbac_service import rbac_service

    is_admin = bool(
        getattr(user, "is_platform_admin", False)
        or getattr(user, "is_org_admin", False)
        or rbac_service.is_org_admin_anywhere(user)
    )
    if not is_admin:
        return False
    return not _admin_mfa_bypass_active()


def _admin_mfa_bypass_active() -> bool:
    """True only for the browser-smoke subprocess's own boot, never in a real
    deployment.

    This is the ONLY way ``required_for()`` can ever return ``False`` for an
    administrator. It requires BOTH:

    - ``current_app.config["ADMIN_MFA_BYPASS"]``, a hardcoded class attribute
      declared on ``config.py``'s ``SmokeTestingConfig`` alone -- never read
      from an environment variable, a request, a header, a query parameter,
      a session key or a database setting; and
    - ``current_app.testing`` (Flask's own ``TESTING`` flag), so a config
      class that copies the attribute without genuinely being a testing
      config is still refused -- ``app/__init__.py``'s ``create_app()``
      raises at boot in that case and never reaches a point where this
      function could be called.

    Returns False with no app context at all (e.g. a plain function call in
    a unit test with no request in flight), which is the safe default: the
    gate is enforced unless an app explicitly says otherwise.
    """
    try:
        from flask import current_app

        app = current_app._get_current_object()
    except RuntimeError:
        return False
    return bool(app.config.get("ADMIN_MFA_BYPASS")) and bool(app.testing)


FIRST_WEEK_DAYS = 7

# Why the first-week window is closed, in the words the enrolment page shows.
# The keys are the closing conditions, in the order they are tested.
_WINDOW_CLOSED_REASONS = {
    "enrolled": "Two-factor sign-in is already on.",
    "not_owner": "Your account is not the sole owner of a workspace you signed up for.",
    "week_over": "Your first week is over.",
    "second_member": "Your workspace now has a second member.",
    "open_invitation": "Your workspace has an invitation waiting to be accepted.",
    "required": "Your workspace requires two-factor sign-in.",
    "credential": "Your workspace now has an API credential.",
    "write_access": "Your workspace now has write access to a connected source.",
    "single_sign_on": "Your workspace now has single sign-on.",
}


def _first_week_end(user):
    from datetime import timedelta

    signed_up = getattr(user, "signup_at", None)
    return signed_up + timedelta(days=FIRST_WEEK_DAYS) if signed_up is not None else None


def _window_closed_by(user):
    """The key of the first condition that keeps the first-week window shut for
    ``user``, or ``None`` when every condition holds. Read from the database on
    every call; nothing stored, configured, requested or held in a session can
    open it."""
    from datetime import datetime

    from app import db
    from app.models.org_role import OrgRole
    from app.models.organization import Organization
    from app.models.pending_invitation import PendingInvitation
    from app.models.sso_config import SSOConfig
    from app.models.user import User
    from app.services.rbac_service import rbac_service

    if getattr(user, "mfa_enabled", False):
        return "enrolled"
    org_id = getattr(user, "organization_id", None)
    ends = _first_week_end(user)
    if getattr(user, "is_platform_admin", False) or org_id is None or ends is None:
        return "not_owner"
    if not rbac_service.is_org_admin(user, org_id):
        return "not_owner"
    # Administrator anywhere else, however it was granted, ends the exemption.
    if any(rbac_service.is_org_admin(user, other) for other in rbac_service.org_ids_for(user) - {org_id}):
        return "not_owner"
    if datetime.utcnow() >= ends:
        return "week_over"

    # tenant-scoping-ok: every read below names this person's own organisation
    members = {row[0] for row in db.session.query(User.id).filter(User.organization_id == org_id)}
    members |= {row[0] for row in db.session.query(OrgRole.user_id).filter(OrgRole.organization_id == org_id)}
    if members != {user.id}:
        return "second_member"
    now = datetime.utcnow()
    invitations = db.session.query(PendingInvitation).filter(PendingInvitation.organization_id == org_id).all()
    if any(not invitation.is_expired(now) for invitation in invitations):
        return "open_invitation"

    org = db.session.query(Organization).filter(Organization.id == org_id).first()
    settings = (org.settings if org is not None else None) or {}
    if settings.get(REQUIRE_SETTING):
        return "required"
    if _holds_credential(org_id, user):
        return "credential"
    if any((settings.get("connector_action_delegation") or {}).values()) or _holds_write_connection(org_id):
        return "write_access"
    if db.session.query(SSOConfig.id).filter(SSOConfig.organization_id == org_id).first() is not None:
        return "single_sign_on"
    return None


REQUIRE_SETTING = "require_two_factor"


def _holds_credential(org_id, user) -> bool:
    """An OAuth token or client, an API token or a service account of this organisation."""
    from app import db
    from app.models.user import User
    from app.modules.oauth_provider.models import OAuthToken

    if db.session.query(OAuthToken.id).filter(
        OAuthToken.organization_id == org_id, OAuthToken.revoked_at.is_(None)
    ).first() is not None:
        return True
    return db.session.query(User.id).filter(
        User.organization_id == org_id, User.is_service_user.is_(True)
    ).first() is not None


def _holds_write_connection(org_id) -> bool:
    """A stored connection that can write to the source: code push, or a
    Lucidchart grant whose scope names a write."""
    from app import db
    from app.models.connector_config import DevOpsConnectorConfig, LucidchartConnectorConfig

    if db.session.query(DevOpsConnectorConfig.id).filter(
        DevOpsConnectorConfig.organization_id == org_id
    ).first() is not None:
        return True
    for (scope,) in db.session.query(LucidchartConnectorConfig.scope).filter(
        LucidchartConnectorConfig.organization_id == org_id
    ):
        if "write" in (scope or "").lower() or "edit" in (scope or "").lower():
            return True
    return False


def may_defer_enrolment(user) -> bool:
    """True when an administrator may finish a sign-in, or keep using a
    session, without having enrolled for two-factor sign-in right now.

    The first-week window of a new workspace's sole owner: open only while
    every condition in ``_window_closed_by`` holds. Computed from the database
    on every call. It never touches ``_admin_mfa_bypass_active`` and nothing
    in it reads an environment variable, a request value or a stored flag.
    """
    if user is None or not required_for(user):
        return False
    return _window_closed_by(user) is None


def must_enrol_now(user) -> bool:
    """True when ``user`` is an administrator who has not enrolled and whose
    first-week window is not open: they are sent to enrol."""
    if user is None or getattr(user, "mfa_enabled", False) or not required_for(user):
        return False
    return not may_defer_enrolment(user)


def step_up_needed(user) -> bool:
    """True when ``user`` must enrol before a sensitive action: an
    administrator who has not enrolled, whether or not the first-week window
    is open."""
    return user is not None and not getattr(user, "mfa_enabled", False) and required_for(user)


def window_closed_reason(user):
    """One plain line saying why the window is shut, or ``None`` while open."""
    key = _window_closed_by(user)
    return _WINDOW_CLOSED_REASONS.get(key) if key else None


def window_ends_at(user):
    """When the first week ends, or ``None`` when the window is not open."""
    return _first_week_end(user) if may_defer_enrolment(user) else None


def window_days_left(user):
    """Whole days left in the first week (at least 1), or ``None`` when closed."""
    from datetime import datetime

    ends = window_ends_at(user)
    if ends is None:
        return None
    seconds = (ends - datetime.utcnow()).total_seconds()
    return max(1, -(-int(seconds) // 86400))


def generate_secret() -> str:
    """A fresh base32 TOTP secret, not yet attached to any user."""
    import pyotp

    return pyotp.random_base32()


def provisioning_uri(user, secret: str) -> str:
    """The ``otpauth://`` URI an authenticator app scans (as text or a QR
    code the template renders), naming this platform and the user's email
    so multiple accounts are distinguishable in the app."""
    import pyotp

    issuer = os.environ.get("MFA_ISSUER_NAME", "Archie EA")
    return pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=issuer)


def verify_code(secret: str, code: str) -> bool:
    """True if ``code`` is a currently-valid TOTP for ``secret``.

    A one-step clock-skew allowance (one window either side) tolerates
    ordinary clock drift between the user's device and the server without
    widening the forgery window to anything a shoulder-surfed code could
    still be replayed into minutes later.
    """
    if not secret or not code:
        return False
    import pyotp

    return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)


def generate_recovery_codes(count: int = 8) -> list[str]:
    """Generate ``count`` recovery codes, each 10 alphanumeric characters."""
    import secrets
    import string

    alphabet = string.ascii_uppercase + string.digits
    return ["".join(secrets.choice(alphabet) for _ in range(10)) for _ in range(count)]


def enroll(user, secret: str, code: str) -> list[str]:
    """Attach ``secret`` to ``user`` and turn MFA on, after proving the
    administrator can already produce a valid code from it (so a typo'd or
    never-scanned secret can never lock the account's own enrolment in).

    Returns a list of recovery codes that the user must save.

    Raises:
        :class:`MFAError` if ``code`` does not verify against ``secret``.
    """
    if not verify_code(secret, code):
        raise MFAError("That code did not match. Re-scan the QR code and try again.")

    from app import db

    user.mfa_secret = secret
    user.mfa_enabled = True
    recovery_codes = generate_recovery_codes()
    import json

    from werkzeug.security import generate_password_hash

    # Only salted hashes are stored; the plain codes exist for this one return.
    user.mfa_recovery_codes = json.dumps([generate_password_hash(c) for c in recovery_codes])
    db.session.commit()
    return recovery_codes


def _normalise_recovery_code(code: str) -> str:
    return (code or "").replace("-", "").replace(" ", "").strip().upper()


def use_recovery_code(user, code: str) -> bool:
    """True if ``code`` is an unused recovery code of ``user``; the matching
    code is removed so it works exactly once."""
    import json

    from werkzeug.security import check_password_hash

    code = _normalise_recovery_code(code)
    if not code or not getattr(user, "mfa_enabled", False) or not user.mfa_recovery_codes:
        return False
    try:
        hashes = json.loads(user.mfa_recovery_codes)
    except ValueError:
        return False
    for stored in hashes:
        if check_password_hash(stored, code):
            from app import db

            hashes.remove(stored)
            user.mfa_recovery_codes = json.dumps(hashes)
            db.session.commit()
            return True
    return False


def verify_login_code(user, code: str) -> bool:
    """True if ``code`` is currently valid for ``user``'s enrolled secret.

    False (never raises) when the user has no secret enrolled yet --
    callers decide what "no secret" means for their flow (enrolment vs.
    refusal); this function only answers "does this code match".
    """
    if not getattr(user, "mfa_enabled", False) or not getattr(user, "mfa_secret", None):
        return False
    return verify_code(user.mfa_secret, code)
