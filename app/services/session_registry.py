"""Single accessor for the server-side session registry (``user_sessions``).

Everything that mints, checks or revokes a session record goes through this
module — no inline ``UserSession.query`` in routes or other services (ADR
0008: one accessor per concept).
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models.user_session import UserSession

logger = logging.getLogger(__name__)

#: Only write last_seen_at when it is older than this, so an authenticated
#: session does not take a DB write on every single request.
_TOUCH_THROTTLE_SECONDS = 60


def _request_context():
    """Return (ip, user_agent) for the current request, (None, None) outside one."""
    try:
        from flask import request

        fwd = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        ip = fwd or request.remote_addr or None
        ua = (request.headers.get("User-Agent") or "")[:255] or None
        return (ip[:45] if ip else None), ua
    except Exception as exc:
        logger.debug("session_registry: no request context (%s)", exc)
        return None, None


def issue(user, remember=False):
    """Mint a new session record for ``user``, store its id in the Flask
    session under ``_sid``, and return the sid.

    Caller is responsible for having already called ``login_user()``.
    """
    from flask import session

    sid = secrets.token_urlsafe(32)
    ip, ua = _request_context()
    row = UserSession(
        sid=sid,
        user_id=user.id,
        organization_id=getattr(user, "organization_id", None),
        created_at=datetime.now(timezone.utc),
        ip=ip,
        user_agent=ua,
    )
    db.session.add(row)
    db.session.commit()
    session["_sid"] = sid
    return sid


def login_and_register(user, remember=False):
    """Session-fixation-safe login: clear session, log in, mint a registry
    record. The single replacement for all nine ``login_user(...)`` call
    sites."""
    from flask import session
    from flask_login import login_user

    session.clear()
    login_user(user, remember=remember)
    session.permanent = True
    issue(user, remember=remember)


def is_active(sid):
    """True if ``sid`` names a row that exists and is not revoked.

    Fails closed: any DB error is treated as "not active" so authentication
    degrades to logged-out rather than silently trusting an unverifiable
    session. Postgres being unreachable already breaks every other page, so
    this adds no new single point of failure.
    """
    if not sid:
        return False
    try:
        row = db.session.get(UserSession, sid)
    except Exception:
        logger.error("session_registry: is_active DB lookup failed for sid=%s...", sid[:8], exc_info=True)
        return False
    return row is not None and row.revoked_at is None


def touch(sid):
    """Best-effort ``last_seen_at`` update, throttled to avoid a write on
    every request. Never raises."""
    if not sid:
        return
    try:
        row = db.session.get(UserSession, sid)
        if row is None or row.revoked_at is not None:
            return
        now = datetime.now(timezone.utc)
        last_seen = row.last_seen_at
        if last_seen is not None and last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        if last_seen is not None and (now - last_seen) < timedelta(seconds=_TOUCH_THROTTLE_SECONDS):
            return
        row.last_seen_at = now
        db.session.commit()
    except Exception:
        logger.debug("session_registry: touch failed for sid=%s...", sid[:8] if sid else "?", exc_info=True)
        db.session.rollback()


def revoke(sid, reason):
    """Mark a session revoked. Idempotent -- revoking an already-revoked or
    unknown sid is a no-op, not an error."""
    if not sid:
        return
    try:
        row = db.session.get(UserSession, sid)
        if row is None or row.revoked_at is not None:
            return
        row.revoked_at = datetime.now(timezone.utc)
        row.revoked_reason = reason
        db.session.commit()
    except Exception:
        logger.error("session_registry: revoke failed for sid=%s... reason=%s", sid[:8] if sid else "?", reason, exc_info=True)
        db.session.rollback()
        raise


def revoke_all_for_user(user_id, reason, except_sid=None):
    """Revoke every currently-active session for ``user_id`` except
    ``except_sid``. Returns the number of sessions revoked.

    Raises on DB failure -- callers that must not fail the caller's own
    unrelated success path (e.g. a password change that already committed)
    are responsible for catching this and degrading their own messaging;
    this function itself must not silently report success it did not
    achieve, per the fabricated-data rule.
    """
    try:
        # tenant-scoping-ok: UserSession is deliberately NOT TenantMixin (see
        # its module docstring) -- it is keyed by user_id, which already
        # implies a single account/org, and is read pre-tenant-context on
        # every authenticated request.
        q = UserSession.query.filter(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
        )
        if except_sid:
            q = q.filter(UserSession.sid != except_sid)
        rows = q.all()
        now = datetime.now(timezone.utc)
        for row in rows:
            row.revoked_at = now
            row.revoked_reason = reason
        db.session.commit()
        return len(rows)
    except Exception:
        logger.error("session_registry: revoke_all_for_user failed for user_id=%s reason=%s", user_id, reason, exc_info=True)
        db.session.rollback()
        raise


def purge_expired(older_than):
    """Delete registry rows revoked (or created, if never revoked) before
    ``older_than`` (a ``datetime``). Returns the number of rows deleted.

    Housekeeping only -- deleting a row here has no effect on whether a
    session is accepted (a missing row is already treated as inactive by
    ``is_active``), so this is safe to run at any time.
    """
    try:
        # tenant-scoping-ok: UserSession is deliberately NOT TenantMixin (see
        # its module docstring); housekeeping sweep across all orgs by design.
        deleted = UserSession.query.filter(
            db.or_(
                db.and_(UserSession.revoked_at.isnot(None), UserSession.revoked_at < older_than),
                db.and_(UserSession.revoked_at.is_(None), UserSession.created_at < older_than),
            )
        ).delete(synchronize_session=False)
        db.session.commit()
        return deleted
    except Exception:
        logger.error("session_registry: purge_expired failed", exc_info=True)
        db.session.rollback()
        raise
