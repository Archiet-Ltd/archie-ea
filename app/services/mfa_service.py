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
            mfa_service.enroll(user, secret, code)
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


def enroll(user, secret: str, code: str) -> None:
    """Attach ``secret`` to ``user`` and turn MFA on, after proving the
    administrator can already produce a valid code from it (so a typo'd or
    never-scanned secret can never lock the account's own enrolment in).

    Raises:
        :class:`MFAError` if ``code`` does not verify against ``secret``.
    """
    if not verify_code(secret, code):
        raise MFAError("That code did not match. Re-scan the QR code and try again.")

    from app import db

    user.mfa_secret = secret
    user.mfa_enabled = True
    db.session.commit()


def verify_login_code(user, code: str) -> bool:
    """True if ``code`` is currently valid for ``user``'s enrolled secret.

    False (never raises) when the user has no secret enrolled yet --
    callers decide what "no secret" means for their flow (enrolment vs.
    refusal); this function only answers "does this code match".
    """
    if not getattr(user, "mfa_enabled", False) or not getattr(user, "mfa_secret", None):
        return False
    return verify_code(user.mfa_secret, code)
