"""Error aggregation surface: a client-error sink + the admin viewing page.

Server-side WARNING+ log records are captured automatically by
``app/_bootstrap/error_tracking.py``. This file covers the other half: a
browser has no equivalent of "the server logged it" — a JS exception just
vanishes unless something ships it somewhere. ``POST /api/client-error`` is
that somewhere; ``core/05-error.js`` calls it from the existing
``window.onerror``/``unhandledrejection`` handlers and from
``Platform.error.handle``.

Both write and read paths share the same dedup logic as the server-side
handler (fingerprint + occurrence_count), fixing them together into one
"ErrorEvent" system of record rather than two disagreeing tables.
"""

import hashlib
import logging
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user

from app.extensions import csrf, db
from app.middleware.tenant_decorators import platform_admin_required
from app.models.error_event import ErrorEvent

error_events_bp = Blueprint("error_events", __name__)
logger = logging.getLogger(__name__)

_MAX_MESSAGE_LEN = 2000
_MAX_STACK_LEN = 8000


def _fingerprint(source, location, message):
    basis = "%s:%s:%s" % (source, location, str(message)[:120])
    return hashlib.sha256(basis.encode("utf-8", "replace")).hexdigest()[:32]


@error_events_bp.route("/api/client-error", methods=["POST"])
def client_error():
    """Ingest one client-side JS error report.

    Not @login_required — a JS error on the login page itself, or one hit by
    a session that has already expired, is exactly the kind this needs to
    catch, not silently drop for lack of auth. Not tenant-scoped for the same
    reason ``ErrorEvent`` itself isn't: it is attributed via a nullable
    organization_id, not filtered by one.
    """
    data = request.get_json(force=True, silent=True)
    if not data or not isinstance(data, dict):
        return "", 400

    message = str(data.get("message") or "Unknown client error")[:_MAX_MESSAGE_LEN]
    location = str(data.get("location") or "unknown")[:500]
    stack = data.get("stack")
    if stack:
        stack = str(stack)[:_MAX_STACK_LEN]
    url = str(data.get("url") or request.referrer or "")[:1000]

    org_id = None
    user_id = None
    if current_user.is_authenticated:
        user_id = current_user.id
        org_id = getattr(current_user, "organization_id", None)

    fingerprint = _fingerprint("client", location, message)
    now = datetime.utcnow()

    # tenant-scoping-ok: platform-wide aggregate by design (see ErrorEvent's docstring); events
    # are deduplicated across organisations and only platform admins read them.
    existing = ErrorEvent.query.filter_by(fingerprint=fingerprint, resolved=False).first()
    if existing:
        existing.occurrence_count = (existing.occurrence_count or 0) + 1
        existing.last_seen_at = now
        # This endpoint is unauthenticated, so a repeat report must not replace a stack
        # an operator may read: it only fills one in when none was recorded.
        if stack and not existing.stack:
            existing.stack = stack
    else:
        db.session.add(ErrorEvent(
            fingerprint=fingerprint,
            source="client",
            level=str(data.get("level") or "ERROR")[:16],
            message=message,
            location=location,
            stack=stack,
            url=url,
            user_agent=request.headers.get("User-Agent", "")[:500],
            organization_id=org_id,
            user_id=user_id,
            occurrence_count=1,
            first_seen_at=now,
            last_seen_at=now,
            resolved=False,
        ))
    db.session.commit()
    return "", 204


# csrf.exempt: fired from a global window.onerror/unhandledrejection handler,
# not a user-initiated form submission — the same rationale as /api/csp-report.
csrf.exempt(client_error)


@error_events_bp.route("/admin/errors", methods=["GET"])
@platform_admin_required
def errors_dashboard():
    """Platform-admin view of aggregated, deduplicated errors.

    Cross-tenant by design (see ErrorEvent's docstring) -- this is the answer
    to "how do we know the system has silently degraded", which is a platform
    question, not a per-org one.
    """
    show_resolved = request.args.get("resolved") == "1"
    source_filter = request.args.get("source") or ""

    query = ErrorEvent.query  # tenant-scoping-ok: platform-admin-only cross-tenant view, gated by @platform_admin_required
    if not show_resolved:
        query = query.filter_by(resolved=False)
    if source_filter in ("server", "client"):
        query = query.filter_by(source=source_filter)

    events = query.order_by(ErrorEvent.last_seen_at.desc()).limit(200).all()

    return render_template(
        "monitoring/errors_dashboard.html",
        events=events,
        show_resolved=show_resolved,
        source_filter=source_filter,
    )


@error_events_bp.route("/admin/errors/<int:event_id>/resolve", methods=["POST"])
@platform_admin_required
def resolve_error(event_id):
    event = ErrorEvent.query.get_or_404(event_id)  # tenant-scoping-ok: platform-admin-only, gated by @platform_admin_required
    event.resolved = True
    event.resolved_at = datetime.utcnow()
    db.session.commit()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"success": True})
    from flask import redirect, url_for
    return redirect(url_for("error_events.errors_dashboard"))
