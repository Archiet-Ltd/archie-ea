"""Where each sign-up came from, how far it got, and who is stuck.

The register link carries ``utm_source``, ``utm_campaign`` and ``ref``. They are read on the first request that
carries any of them, held in the visitor's session through the register, confirm and two-factor steps, and
stored once on the account that sign-up creates (``users.signup_*``). Nothing is sent to a third party and no
cookie other than the session is added.

Platform admins read the result on ``/admin/signups``. Two scheduled jobs use the same facts: a check every five
minutes that mails the founder once when an account has not reached its first answer 30 minutes after sign-up,
and a daily report of the last day's sign-ups. The founder's address is configuration (``FOUNDER_ALERT_EMAIL``).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from flask import has_request_context, request, session

logger = logging.getLogger(__name__)

SESSION_KEY = "_signup_origin"
MAX_LENGTH = 64
STUCK_AFTER = timedelta(minutes=30)
# An account older than this is history, not news: switching the check on never mails about last month.
STALE_AFTER = timedelta(days=3)
_PARAMS = (("utm_source", "source"), ("utm_campaign", "campaign"), ("ref", "ref"))
_ALLOWED = re.compile(r"[^A-Za-z0-9 _.:/+@~-]")

# The steps, in order. The last one a person has reached is their step.
STEPS: Tuple[Tuple[str, str], ...] = (
    ("signed_up", "Signed up"),
    ("address_confirmed", "Address confirmed"),
    ("profile_confirmed", "Profile confirmed"),
    ("first_answer", "First answer"),
)
_NEXT = {
    "signed_up": "confirm the e-mail address",
    "address_confirmed": "confirm the company profile",
    "profile_confirmed": "ask a first question in the AI chat",
}


def clean(value: Any) -> Optional[str]:
    """Plain text, limited in length; None when nothing usable is left."""
    if not isinstance(value, str):
        return None
    text = _ALLOWED.sub("", value).strip()[:MAX_LENGTH].strip()
    return text or None


def capture() -> None:
    """Keep the three values from this request unless an earlier one already supplied them."""
    if SESSION_KEY in session:
        return
    found = {key: clean(request.args.get(param)) for param, key in _PARAMS}
    if any(found.values()):
        session[SESSION_KEY] = found


def held() -> Dict[str, Optional[str]]:
    """The values this visitor arrived with (empty values when they arrived without any)."""
    kept = session.get(SESSION_KEY) if has_request_context() else None
    kept = kept if isinstance(kept, dict) else {}
    return {key: clean(kept.get(key)) for _param, key in _PARAMS}


def record(user, origin: Optional[Dict[str, Optional[str]]] = None) -> None:
    """Store, once, where this new account came from and when it signed up."""
    from app import db

    if user.signup_at is not None:
        return
    origin = origin if origin is not None else held()
    user.signup_source = origin.get("source")
    user.signup_campaign = origin.get("campaign")
    user.signup_ref = origin.get("ref")
    user.signup_at = datetime.utcnow()
    db.session.commit()


def install_signup_origin(app) -> None:
    @app.before_request
    def _keep_signup_origin():
        # Only a page request that actually carries a value touches the session.
        if request.method == "GET" and request.args and (
            "utm_source" in request.args or "utm_campaign" in request.args or "ref" in request.args
        ):
            capture()


# -- what a person has done -------------------------------------------------------------------------


def _first_answer_at(user) -> Optional[datetime]:
    from sqlalchemy import func

    from app import db
    from app.models.conversation import ConversationMessageRecord, ConversationThreadRecord

    # tenant-scoping-ok: platform-wide report; the thread is matched on this user and their organisation
    return db.session.query(func.min(ConversationMessageRecord.created_at)).join(
        ConversationThreadRecord, ConversationThreadRecord.id == ConversationMessageRecord.thread_id
    ).filter(
        ConversationThreadRecord.user_id == user.id,
        ConversationThreadRecord.organization_id == user.organization_id,
        ConversationMessageRecord.role == "assistant",
    ).scalar()


def step_of(user) -> Tuple[str, str]:
    """(key, label) of the furthest step this account has reached."""
    from app.modules.account.services import onboarding_service

    if _first_answer_at(user) is not None:
        return STEPS[3]
    org = user.organization
    if org is not None and onboarding_service.profile_confirmed_at(org) is not None:
        return STEPS[2]
    return STEPS[1] if user.confirmed else STEPS[0]


def minutes_to_first_answer(user) -> Optional[float]:
    answered = _first_answer_at(user)
    if answered is None or user.signup_at is None:
        return None
    return max((answered - user.signup_at).total_seconds() / 60.0, 0.0)


def rows(limit: int = 200) -> List[Dict[str, Any]]:
    """The newest self-serve sign-ups, for the Sign-ups list."""
    from app.models.user import User

    users = (
        User.query.filter(User.signup_at.isnot(None))  # tenant-scoping-ok: platform-admin report across organisations
        .order_by(User.signup_at.desc()).limit(limit).all()
    )
    out = []
    for user in users:
        key, label = step_of(user)
        minutes = minutes_to_first_answer(user)
        out.append({
            "email": user.email,
            "name": ("%s %s" % (user.first_name or "", user.last_name or "")).strip(),
            "source": user.signup_source, "campaign": user.signup_campaign, "ref": user.signup_ref,
            "step_key": key, "step": label, "signed_up_at": user.signup_at,
            "minutes_to_first_answer": minutes,
            "time_to_first_answer": ("%d min" % round(minutes)) if minutes is not None else "Not yet",
        })
    return out


# -- the founder's mail -----------------------------------------------------------------------------


def _send(app, to: str, subject: str, template: str, **context):
    """Send one message as a scheduled job does: outside any request, so the page context a template expects is
    given a request of its own."""
    from app.flask_email import deliver_email

    with app.test_request_context("/"):
        return deliver_email(to, subject, template, **context)


def _founder(app) -> Optional[str]:
    return (app.config.get("FOUNDER_ALERT_EMAIL") or "").strip() or None


def _line(user, key: str, label: str) -> str:
    return "%s | ref %s | source %s | campaign %s | stopped after: %s" % (
        user.email, user.signup_ref or "none", user.signup_source or "none", user.signup_campaign or "none", label)


def alert_stuck_accounts(app, now: Optional[datetime] = None) -> List[int]:
    """Mail the founder once per account that has not reached a first answer 30 minutes after sign-up.

    Returns the ids alerted. An account already alerted, a completed one, one younger than 30 minutes and one
    older than three days are skipped. With no founder address, or when the message cannot be sent, nothing is
    marked, so the next run tries again.
    """
    from app import db
    from app.models.user import User

    to = _founder(app)
    if not to:
        logger.warning("Sign-up alert: FOUNDER_ALERT_EMAIL is not set, so no alert is sent")
        return []
    now = now or datetime.utcnow()
    candidates = User.query.filter(  # tenant-scoping-ok: platform job across organisations
        User.signup_at.isnot(None), User.signup_alerted_at.is_(None),
        User.signup_at <= now - STUCK_AFTER, User.signup_at >= now - STALE_AFTER,
    ).order_by(User.signup_at).all()
    alerted = []
    for user in candidates:
        key, label = step_of(user)
        if key == "first_answer":
            continue
        minutes = int((now - user.signup_at).total_seconds() // 60)
        delivered, error = _send(
            app, to, "New sign-up stuck: %s" % user.email, "account/email/signup_alert",
            line=_line(user, key, label), user=user, step_label=label, next_step=_NEXT.get(key, ""), minutes=minutes)
        if not delivered:
            logger.warning("Sign-up alert for user %s not sent: %s", user.id, error)
            continue
        user.signup_alerted_at = now
        db.session.commit()
        alerted.append(user.id)
    return alerted


def send_daily_report(app, now: Optional[datetime] = None) -> bool:
    """Mail the founder the last day's sign-ups with where each came from and how far it got."""
    from app.models.user import User

    to = _founder(app)
    if not to:
        logger.warning("Sign-up report: FOUNDER_ALERT_EMAIL is not set, so no report is sent")
        return False
    now = now or datetime.utcnow()
    since = now - timedelta(days=1)
    users = User.query.filter(  # tenant-scoping-ok: platform job across organisations
        User.signup_at.isnot(None), User.signup_at >= since).order_by(User.signup_at).all()
    lines = []
    for user in users:
        key, label = step_of(user)
        lines.append(_line(user, key, label))
    delivered, error = _send(
        app, to, "Sign-ups in the last day: %d new" % len(users), "account/email/signup_report",
        lines=lines, count=len(users))
    if not delivered:
        logger.warning("Sign-up report not sent: %s", error)
    return bool(delivered)
