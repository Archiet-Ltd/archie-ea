"""The activity record: what each signed-in person did, and how it ended.

Two kinds of entry go into the one audit store (``AuditLog``, hash-chained):

* **Page use** - one entry per signed-in request: who, which organisation,
  the route name, the method, the outcome (succeeded, refused or failed) and
  how long it took. The Settings switch "Record page use" turns this kind off.
* **Security events** - sign-in, failed sign-in, two-factor challenge and
  recovery-code use, password reset, export, a refused permission and an
  attempt to reach another organisation. These are always recorded; no
  setting turns them off.

Neither kind holds a request body, a secret or any personal data beyond the
user id. Entries are kept for 400 days (``purge_expired``).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

PAGE_USE_TABLE = "page_use"
SECURITY_TABLE = "security_event"
PAGE_USE_ACTION = "request"
SECURITY_ACTION = "security"
RETENTION_ACTION = "retention"
RETENTION_DAYS = 400

SUCCEEDED, REFUSED, FAILED = "succeeded", "refused", "failed"

#: The Settings key behind the switch. Kept as the id the page already saves.
SETTING_KEY = "audit-logging"
_SETTING_TTL_SECONDS = 10.0
_setting_cache: dict[str, object] = {"at": 0.0, "value": True}

#: Security events, with the plain sentence a person reads for each.
EVENTS = {
    "sign_in": "Signed in",
    "sign_in_failed": "Sign-in failed",
    "two_factor_passed": "Two-factor code accepted",
    "two_factor_failed": "Two-factor code refused",
    "recovery_code_used": "Recovery code used",
    "password_reset": "Password reset",
    "export": "Data exported",
    "permission_refused": "Permission refused",
    "cross_organisation_attempt": "Attempt to reach another organisation",
}

#: Endpoints that are not page use: assets, health checks and background polls.
_SKIPPED_ENDPOINTS = ("static", "health", "healthz", "version")


def outcome_for(status_code: int) -> str:
    """succeeded below 400, refused for 401/403, failed for every other error."""
    if status_code < 400:
        return SUCCEEDED
    if status_code in (401, 403):
        return REFUSED
    return FAILED


def clear_setting_cache() -> None:
    _setting_cache["at"] = 0.0


def page_use_enabled() -> bool:
    """Whether page use is recorded. On unless the switch was saved off."""
    now = time.monotonic()
    if now - float(_setting_cache["at"]) < _SETTING_TTL_SECONDS:
        return bool(_setting_cache["value"])
    import json

    from app.extensions import db

    value = True
    try:
        raw = db.session.execute(
            db.text("SELECT value FROM system_settings WHERE key = :key"),  # tenancy-ok: platform settings table has no organisation column
            {"key": SETTING_KEY},
        ).scalar()
        if raw is not None:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            value = parsed not in (False, "false", "False", 0)
    except Exception:
        db.session.rollback()
        logger.debug("activity record: switch unreadable, recording stays on", exc_info=True)
    _setting_cache.update(at=now, value=value)
    return value


def record_security_event(event: str, user=None, *, organization_id=None, **detail):
    """Write one security event. Never raises; never switched off.

    ``detail`` holds short facts such as the route name; callers must not pass
    bodies, secrets or addresses.
    """
    from app.models.audit_log import AuditLog
    from app.services import auth_audit

    if event not in EVENTS:
        raise ValueError("unknown security event: %s" % event)
    ip, ua = auth_audit._request_context()
    user_id = getattr(user, "id", None)
    org_id = organization_id if organization_id is not None else getattr(user, "organization_id", None)
    return AuditLog.log(
        action=SECURITY_ACTION,
        table_name=SECURITY_TABLE,
        user_id=user_id,
        organization_id=org_id,
        record_id=user_id,
        ip_address=ip,
        user_agent=ua,
        new_value={"event": event, **detail},
    )


def record_page_use(user, route: str, method: str, status_code: int, duration_ms: int):
    """Write one page-use entry. Skipped when the switch is off. Never raises."""
    from app.models.audit_log import AuditLog

    if not page_use_enabled():
        return None
    return AuditLog.log(
        action=PAGE_USE_ACTION,
        table_name=PAGE_USE_TABLE,
        user_id=user.id,
        organization_id=getattr(user, "organization_id", None),
        record_id=user.id,
        new_value={
            "route": route,
            "method": method,
            "outcome": outcome_for(status_code),
            "status": status_code,
            "duration_ms": duration_ms,
        },
    )


def purge_expired(now: datetime | None = None) -> int:
    """Remove entries older than 400 days, oldest first, per organisation.

    The chain stays checkable: before a prefix is removed one retention entry
    records the last removed entry's id and seal, and ``verify_chain`` accepts
    the first remaining entry when it follows exactly that seal.
    Returns the number of entries removed.
    """
    from app.extensions import db
    from app.models.audit_log import AuditLog

    cutoff = (now or datetime.utcnow()) - timedelta(days=RETENTION_DAYS)
    table = AuditLog.__table__
    removed = 0
    org_ids = [r[0] for r in db.session.execute(
        db.select(table.c.organization_id).where(table.c.created_at < cutoff).distinct()
    ).all()]
    for org_id in org_ids:
        last = db.session.execute(
            db.select(table.c.id, table.c.row_hash)
            .where(AuditLog.org_predicate(org_id), table.c.created_at < cutoff)
            .order_by(table.c.id.desc()).limit(1)
        ).first()
        if last is None:
            continue
        # Only a prefix may go: everything up to the newest old entry, and only
        # when every entry up to it is itself old (ids rise with time).
        AuditLog.log(
            action=RETENTION_ACTION,
            table_name=AuditLog.__tablename__,
            organization_id=org_id,
            new_value={"through_id": last.id, "through_row_hash": last.row_hash,
                       "cutoff": cutoff.isoformat()},
        )
        result = db.session.execute(
            db.delete(table).where(AuditLog.org_predicate(org_id), table.c.id <= last.id)
        )
        removed += result.rowcount or 0
    db.session.commit()
    return removed


def record_request(response):
    """After-request hook body: page use, plus refusal and export events."""
    from flask import g, request
    from flask_login import current_user

    try:
        if not current_user.is_authenticated:
            return response
        endpoint = request.endpoint or ""
        if not endpoint or endpoint.rsplit(".", 1)[-1] in _SKIPPED_ENDPOINTS:
            return response
        status = response.status_code
        started = getattr(g, "_activity_started", None)
        duration_ms = int((time.monotonic() - started) * 1000) if started else 0
        record_page_use(current_user, endpoint, request.method, status, duration_ms)
        if status == 403:
            event = "cross_organisation_attempt" if getattr(g, "audit_cross_organisation", False) else "permission_refused"
            record_security_event(event, current_user, route=endpoint, method=request.method)
        elif status < 400 and "attachment" in response.headers.get("Content-Disposition", "").lower():
            record_security_event("export", current_user, route=endpoint, method=request.method)
    except Exception:
        logger.warning("activity record: could not record %s", request.path, exc_info=True)
    return response


def mark_started() -> None:
    from flask import g

    g._activity_started = time.monotonic()
