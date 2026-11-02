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
    """True when ``user`` is a person who sets up and uses two-factor sign-in:
    every human account, owner or member alike (a service account signs in
    with a token, never a code). Whether an account that has not enrolled may
    carry on for now is the first-week window below.

    The one exception is ``_admin_mfa_bypass_active()`` below -- see its own
    docstring for exactly what that is and is not."""
    return user is not None and not getattr(user, "is_service_user", False) and not _admin_mfa_bypass_active()


def is_administrator(user) -> bool:
    """True for a platform admin or an administrator of any organisation the
    user belongs to. Asked once per request. Sensitive actions (inviting,
    adding a member, connecting a source that can write) ask an administrator
    to enrol at once, whether or not the first-week window is open.

    "Administrator" is any of three things: a platform admin
    (``is_platform_admin``); an admin of the user's own home organisation
    (``user.is_org_admin``); or an organisation admin of ANY organisation the
    user belongs to, their own home organisation or one they were invited into
    (``rbac_service.is_org_admin_anywhere``). An administrator of only a
    deactivated organisation still counts: deactivation is not enforced at
    login or at session-switch time, so this fails closed on that fact (see
    ``rbac_service.org_ids_for``)."""
    if user is None or getattr(user, "is_service_user", False) or _admin_mfa_bypass_active():
        return False
    cache = _this_request_cache()
    key = getattr(user, "id", None)
    if cache is not None and key is not None and key in cache:
        return cache[key]
    from app.services.rbac_service import rbac_service

    answer = bool(
        getattr(user, "is_platform_admin", False)
        or getattr(user, "is_org_admin", False)
        or rbac_service.is_org_admin_anywhere(user)
    )
    if cache is not None and key is not None:
        cache[key] = answer
    return answer


def _this_request_cache():
    """A dict that lives for the current request only, or ``None`` outside one.

    Kept on ``flask.g`` beside the request it belongs to: ``g`` can outlive a
    request when an application context is held open (tests, shells), so an
    answer from an earlier request is never reused."""
    from flask import g, has_request_context, request

    if not has_request_context():
        return None
    current = request._get_current_object()
    held = getattr(g, "_mfa_required_for", None)
    if held is None or held[0] is not current:
        held = (current, {})
        g._mfa_required_for = held
    return held[1]


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

REQUIRE_SETTING = "require_two_factor"

# Why the first-week window is closed, in the words the enrolment page shows.
_WINDOW_CLOSED_REASONS = {
    "enrolled": "Two-factor sign-in is already on.",
    "week_over": "Your first week is over.",
    "required": "Your organisation requires two-factor sign-in.",
}


def _utcnow():
    """The moment the first-week window is judged against; one place so a test can fix it."""
    from datetime import datetime

    return datetime.utcnow()


def _first_week_start(user):
    """When this person's first week began: the later of the sign-up time and
    the time recorded at their first sign-in after the window rule shipped.
    ``None`` for an account that has not signed in since."""
    marks = [
        mark
        for mark in (getattr(user, "signup_at", None), getattr(user, "mfa_window_started_at", None))
        if mark is not None
    ]
    return max(marks) if marks else None


def _first_week_end(user):
    from datetime import timedelta

    started = _first_week_start(user)
    return started + timedelta(days=FIRST_WEEK_DAYS) if started is not None else None


def start_window_if_due(user) -> None:
    """Start the first week at this person's first sign-in when nothing has
    started it yet (an account made without a sign-up record, such as an
    invited member). Written once and never moved."""
    if user is None or _first_week_start(user) is not None:
        return
    if getattr(user, "mfa_enabled", False) or getattr(user, "mfa_enrolled_at", None) is not None:
        return
    from sqlalchemy import update
    from sqlalchemy.orm import Session
    from sqlalchemy.orm.attributes import set_committed_value

    from app import db
    from app.models.user import User

    started = _utcnow()
    # Written through a session of its own, so the request's session is not
    # committed here and nothing else pending in it is saved with the stamp.
    with Session(bind=db.session.get_bind(), join_transaction_mode="create_savepoint") as stamp:
        stamp.execute(
            update(User.__table__)
            .where(User.__table__.c.id == user.id, User.__table__.c.mfa_window_started_at.is_(None))
            .values(mfa_window_started_at=started)
        )
        stamp.commit()
    set_committed_value(user, "mfa_window_started_at", started)


def _required_by_organisation(user) -> bool:
    """True when an organisation this person administers has switched on
    "Require two-factor sign-in" on the Team page. That ends the first-week
    allowance for its administrators from that moment; a member who does not
    administer it keeps the week. Switching it off gives back what the
    window rule says."""
    if user is None or getattr(user, "id", None) is None:
        return False
    cache = _this_request_cache()
    key = ("required_by_organisation", user.id)
    if cache is not None and key in cache:
        return cache[key]
    from app import db
    from app.models.organization import Organization
    from app.services.rbac_service import rbac_service

    ids = rbac_service.org_ids_for(user)
    rows = db.session.query(Organization.id, Organization.settings).filter(Organization.id.in_(ids)).all() if ids else []
    answer = any(
        (settings or {}).get(REQUIRE_SETTING) and rbac_service.is_org_admin(user, org_id)
        for org_id, settings in rows
    )
    if cache is not None:
        cache[key] = answer
    return answer


def _window_closed_by(user):
    """``"enrolled"`` or ``"week_over"`` when the first-week window is shut for
    ``user``, ``None`` while it is open. The same for every human account:
    nothing stored, configured, requested or held in a session can open it,
    and nothing about the workspace (members, invitations, credentials)
    changes its length. An account whose week has not started yet is inside it."""
    if getattr(user, "mfa_enabled", False) or getattr(user, "mfa_enrolled_at", None) is not None:
        return "enrolled"
    ends = _first_week_end(user)
    if _required_by_organisation(user):
        return "required"
    if ends is not None and _utcnow() >= ends:
        return "week_over"
    return None


# Connection types that only read from the source. Everything else, including a
# type this list does not know, can write to it and asks for enrolment first.
# ServiceNow is listed because the one method that writes to it,
# ServiceNowConnectorService.push_arb_decision, has no caller. The day it gains
# one, ServiceNow must leave this list; tests/test_mfa_first_week.py fails then.
READ_ONLY_CONNECTOR_TYPES = frozenset({"github", "servicenow", "ea_tool", "abacus"})


def connector_can_write(connector_type) -> bool:
    """True unless ``connector_type`` is known to only read from its source."""
    return (connector_type or "").strip().lower() not in READ_ONLY_CONNECTOR_TYPES


def may_defer_enrolment(user) -> bool:
    """True when a person may finish a sign-in, or keep using a session,
    without having enrolled for two-factor sign-in right now.

    The first-week window, the same for every human account: open for seven
    days from the start of the account's first week (``_first_week_start``).
    It never touches ``_admin_mfa_bypass_active`` and nothing
    in it reads an environment variable, a request value or a stored flag.
    """
    if not required_for(user):
        return False
    return _window_closed_by(user) is None


def must_enrol_now(user) -> bool:
    """True when ``user`` has not enrolled and the first-week window is over:
    they are sent to enrol."""
    if user is None or getattr(user, "mfa_enabled", False) or not required_for(user):
        return False
    return not may_defer_enrolment(user)


def step_up_needed(user) -> bool:
    """True when ``user`` must enrol before a sensitive action: an
    administrator who has not enrolled, whether or not the first-week window
    is open."""
    return user is not None and not getattr(user, "mfa_enabled", False) and is_administrator(user)


def window_closed_reason(user):
    """One plain line saying why the window is shut, or ``None`` while open."""
    key = _window_closed_by(user)
    return _WINDOW_CLOSED_REASONS.get(key) if key else None


def window_ends_at(user):
    """When the first week ends, or ``None`` when the window is not open."""
    if not may_defer_enrolment(user):
        return None
    from datetime import timedelta

    return _first_week_end(user) or _utcnow() + timedelta(days=FIRST_WEEK_DAYS)


def window_days_left(user):
    """Whole days left in the first week (at least 1), or ``None`` when closed."""
    ends = window_ends_at(user)
    if ends is None:
        return None
    seconds = (ends - _utcnow()).total_seconds()
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
    if getattr(user, "mfa_enrolled_at", None) is None:
        user.mfa_enrolled_at = _utcnow()
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
