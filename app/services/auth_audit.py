"""Authentication event auditing (finding V-06).

V-06 reported that no authentication event was recorded anywhere, and the
account page said as much: "Last login tracking not available" / "IP tracking
not available".

The first half of that was not quite true, and the way it was untrue is the
actual root cause. ``app/security/audit.py`` *did* log authentication, but into
its own ``audit_events`` table — a store nothing surfaces. The compliance
surface a human actually uses, ``/admin/audit-log``
(``app/modules/admin/v2/routes/admin_routes.py::audit_log_viewer``), reads
``app.models.audit_log.AuditLog`` / ``soc2_audit_log``. So the events existed
and were invisible, which is indistinguishable from not existing.

This module writes authentication events into **that** table — the one the
audit log already reads — and reads them back for the account page. It needs no
schema change: ``soc2_audit_log`` already carries ``ip_address``,
``user_agent`` and ``created_at`` columns.
"""

import logging

logger = logging.getLogger(__name__)

#: ``AuditLog.action`` is String(20); these are the auth verbs, all within it.
ACTION_LOGIN = "login"
ACTION_LOGIN_FAILED = "login_failed"
ACTION_LOGOUT = "logout"
ACTION_SESSIONS_REVOKED = "sessions_revoked"
#: A rejected/replayed session cookie -- the actual attack signal for a
#: captured-cookie scenario (D5, round 2): a revoked or unknown sid being
#: presented again, or a session timing out from inactivity.
ACTION_SESSION_REJECTED = "session_rejected"

#: Recorded as the "table" so the audit-log entity filter can isolate them.
AUTH_TABLE = "auth"


def _request_context():
    """Return (ip, user_agent) for the current request, ('' , '') outside one."""
    try:
        from flask import request

        # X-Forwarded-For is only trustworthy behind a proxy that sets it; the
        # left-most entry is the client as the proxy saw it. remote_addr alone
        # would record the load balancer for every user.
        fwd = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        ip = fwd or request.remote_addr or ""
        ua = (request.headers.get("User-Agent") or "")[:500]
        return ip[:45], ua
    except Exception as exc:
        logger.debug("auth audit: no request context (%s)", exc)
        return "", ""


def record_login_success(user):
    """Record a successful authentication. Never raises."""
    return _record(ACTION_LOGIN, user=user)


def record_login_failure(email=None):
    """Record a failed authentication attempt. Never raises.

    ``user`` is resolved from the submitted email when it matches a real
    account, so failed attempts against an existing account are attributable
    (the signal that matters for credential-stuffing detection). An attempt
    against an unknown address is still recorded, with the address in
    ``extra_json`` and a NULL user_id.
    """
    user = None
    if email:
        try:
            from app.models.user import User

            # A login attempt happens BEFORE any tenant context exists:
            # g.current_org_id is unset at this point, and the whole purpose of
            # the lookup is to discover which org (if any) the address belongs
            # to. Scoping it would make every failed attempt unattributable.
            user = User.query.filter(User.email.ilike(email)).first()  # tenant-scoping-ok: pre-authentication lookup; no tenant context exists yet
        except Exception as exc:
            logger.debug("auth audit: user lookup failed for failed login: %s", exc)
    return _record(ACTION_LOGIN_FAILED, user=user, extra={"attempted_email": email})


def record_logout(user):
    """Record a logout. Never raises."""
    return _record(ACTION_LOGOUT, user=user)


def record_sessions_revoked(user, reason, count):
    """Record that other sessions were revoked for ``user`` (e.g. on a
    password change). Never raises."""
    return _record(ACTION_SESSIONS_REVOKED, user=user, extra={"reason": reason, "count": count})


def record_session_rejected(user, reason):
    """Record that a session was rejected -- revoked/unknown sid replayed, or
    an idle-timeout expiry. Never raises."""
    return _record(ACTION_SESSION_REJECTED, user=user, extra={"reason": reason})


def _record(action, user=None, extra=None, organization_id=None, record_id=None, actor_id=None):
    """Write one authentication/provisioning audit row. Never raises.

    ``organization_id`` is for events with no ``current_user`` to take it from
    (SCIM calls carry only a bearer token). ``actor_id`` is the signed-in
    administrator who caused the event when that is not the subject ``user``;
    ``record_id`` overrides the subject id (a transferred ownership row).
    """
    try:
        from app.models.audit_log import AuditLog

        ip, ua = _request_context()
        payload = {"ip_address": ip, "user_agent": ua}
        if extra:
            payload.update(extra)
        org_id = organization_id if organization_id is not None else getattr(user, "organization_id", None)
        return AuditLog.log(
            action=action,
            table_name=AUTH_TABLE,
            user_id=actor_id if actor_id is not None else getattr(user, "id", None),
            organization_id=org_id,
            record_id=record_id if record_id is not None else getattr(user, "id", None),
            ip_address=ip,
            user_agent=ua,
            new_value=payload,
        )
    except Exception:
        # Authentication must never fail because auditing did.
        logger.warning("auth audit: failed to record %s", action, exc_info=True)
        return None


# --- Provisioning and leaver events (R1-B26 PR 1, TB-0143) -----------------
# Each action is at most 20 characters (AuditLog.action is String(20)). The
# ``extra`` dict records the actor, the names of changed attributes and, for
# transfers, the from/to users and the item -- never a secret or a raw token.
ACTION_SCIM_USER_CREATED = "scim_user_created"
ACTION_SCIM_USER_UPDATED = "scim_user_updated"
ACTION_USER_DEACTIVATED = "user_deactivated"
ACTION_USER_REACTIVATED = "user_reactivated"
ACTION_SCIM_GROUP_CHANGED = "scim_group_changed"
ACTION_SCIM_TOKEN_ISSUED = "scim_token_issued"
ACTION_SCIM_TOKEN_REVOKED = "scim_token_revoked"
ACTION_SCIM_AUTH_FAILED = "scim_auth_failed"
ACTION_OWNER_TRANSFERRED = "owner_transferred"


def _actor_extra(actor, extra):
    payload = {"actor": actor}
    if extra:
        payload.update(extra)
    return payload


def record_scim_user_created(org_id, user, actor, changed=()):
    return _record(ACTION_SCIM_USER_CREATED, user=user, organization_id=org_id,
                   extra=_actor_extra(actor, {"changed": sorted(changed)}))


def record_scim_user_updated(org_id, user, actor, changed=()):
    return _record(ACTION_SCIM_USER_UPDATED, user=user, organization_id=org_id,
                   extra=_actor_extra(actor, {"changed": sorted(changed)}))


def record_user_deactivated(org_id, user, actor, reason, actor_id=None):
    return _record(ACTION_USER_DEACTIVATED, user=user, organization_id=org_id, actor_id=actor_id,
                   record_id=user.id, extra=_actor_extra(actor, {"reason": reason, "subject_user_id": user.id}))


def record_user_reactivated(org_id, user, actor, actor_id=None):
    return _record(ACTION_USER_REACTIVATED, user=user, organization_id=org_id, actor_id=actor_id,
                   record_id=user.id, extra=_actor_extra(actor, {"subject_user_id": user.id}))


def record_scim_group_changed(org_id, group_id, actor, changed=(), extra=None):
    payload = {"group_id": group_id, "changed": sorted(changed)}
    if extra:
        payload.update(extra)
    return _record(ACTION_SCIM_GROUP_CHANGED, organization_id=org_id, record_id=group_id,
                   extra=_actor_extra(actor, payload))


def record_scim_token_issued(org_id, token_id, token_prefix, actor, actor_id=None):
    return _record(ACTION_SCIM_TOKEN_ISSUED, organization_id=org_id, record_id=token_id, actor_id=actor_id,
                   extra=_actor_extra(actor, {"token_prefix": token_prefix}))


def record_scim_token_revoked(org_id, token_id, token_prefix, actor, actor_id=None):
    return _record(ACTION_SCIM_TOKEN_REVOKED, organization_id=org_id, record_id=token_id, actor_id=actor_id,
                   extra=_actor_extra(actor, {"token_prefix": token_prefix}))


def record_scim_auth_failed(reason, organization_id=None, token_prefix=None):
    """A rejected SCIM bearer token. Only the reason and, when the token row
    was found, its prefix are recorded -- never the presented value."""
    extra = {"reason": reason}
    if token_prefix:
        extra["token_prefix"] = token_prefix
    return _record(ACTION_SCIM_AUTH_FAILED, organization_id=organization_id, extra=extra)


def record_owner_transferred(org_id, owner_row_id, from_user_id, to_user_id, actor_id,
                             application_id=None, element_type=None, element_id=None,
                             removed_duplicate=False):
    return _record(
        ACTION_OWNER_TRANSFERRED, organization_id=org_id, record_id=owner_row_id, actor_id=actor_id,
        extra={
            "actor": f"user:{actor_id}",
            "from_user_id": from_user_id,
            "to_user_id": to_user_id,
            "application_id": application_id,
            "element_type": element_type,
            "element_id": element_id,
            "removed_duplicate": bool(removed_duplicate),
        },
    )


def _current_org_id():
    """The request's tenant, or None outside a request context.

    AuditLog is a plain db.Model, not a TenantMixin model, so the ORM event that
    injects the tenant predicate never fires for it — the scoping has to be
    written out at every read. ``user_id`` already implies an organisation, so
    this is defence in depth rather than the primary control; outside a request
    (CLI, scheduler) there is no current org and the user_id predicate stands
    alone.
    """
    try:
        from flask import g, has_request_context

        if has_request_context():
            return getattr(g, "current_org_id", None)
    except Exception as exc:
        logger.debug("auth audit: org scope unavailable: %s", exc)
    return None


def last_login(user_id, before_id=None):
    """Return the most recent successful login for ``user_id``, or ``None``.

    ``before_id`` excludes a specific entry — used by the account page so that
    "last login" means the previous session rather than the row written by the
    request the user is currently making.

    Returns ``None`` when nothing is recorded. Callers must render that as an
    em dash, not as a fabricated value.
    """
    try:
        from app.models.audit_log import AuditLog

        q = AuditLog.query.filter(
            AuditLog.user_id == user_id,
            AuditLog.action == ACTION_LOGIN,
            AuditLog.table_name == AUTH_TABLE,
        )
        org_id = _current_org_id()
        if org_id:
            q = q.filter(AuditLog.organization_id == org_id)
        if before_id is not None:
            q = q.filter(AuditLog.id != before_id)
        return q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).first()
    except Exception:
        logger.warning("auth audit: last_login lookup failed", exc_info=True)
        return None


def recent_auth_events(user_id, limit=5):
    """Return the user's most recent authentication events (newest first)."""
    try:
        from app.models.audit_log import AuditLog

        q = AuditLog.query.filter(
            AuditLog.user_id == user_id,
            AuditLog.table_name == AUTH_TABLE,
        )
        org_id = _current_org_id()
        if org_id:
            q = q.filter(AuditLog.organization_id == org_id)
        return (
            q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(limit)
            .all()
        )
    except Exception:
        logger.warning("auth audit: recent_auth_events lookup failed", exc_info=True)
        return []
