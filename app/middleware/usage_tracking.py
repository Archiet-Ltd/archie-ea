"""
Usage-tracking middleware — registers an after_request hook that fires
UsageMeteringService.record() for key platform events.

Design contract:
- Must be a no-op when g.current_org_id is None (unauthenticated requests).
- Must never raise — every exception is swallowed inside UsageMeteringService.record().
- Currently tracks: user_login events by detecting successful auth endpoints,
  and (R1-B88) every file download, written as one ``export`` audit row.
"""

import logging

from flask import g, request

logger = logging.getLogger(__name__)

# Endpoints that correspond to a successful user login.
_LOGIN_ENDPOINTS = frozenset(
    [
        "auth.login",
        "account.login",
        "main.login",
        "auth.login_post",
    ]
)


def _content_disposition_filename(header):
    """The file name inside a ``Content-Disposition: attachment`` header, or None."""
    from werkzeug.http import parse_options_header

    _, options = parse_options_header(header)
    return options.get("filename")


def _record_export(response, org_id, user_id):
    """R1-B88: one ``export`` audit row for a signed-in, successful file download.

    The only place a download is recorded; ``export_anomaly_service.is_export_row``
    is the only place that decides what counts as one.
    """
    if user_id is None or response.status_code != 200:
        return
    disposition = response.headers.get("Content-Disposition", "")
    if not disposition.lower().startswith("attachment"):
        return

    from app.models.audit_log import AuditLog

    AuditLog.log(
        action="export",
        table_name="export",
        organization_id=org_id,
        user_id=user_id,
        extra_json={
            "endpoint": request.endpoint,
            "path": request.path,
            "filename": _content_disposition_filename(disposition),
            "content_type": response.mimetype,
            "bytes": response.content_length,
        },
    )


def install_usage_tracking(app):
    """Register the after_request usage-tracking handler on *app*."""

    @app.after_request
    def _track_usage(response):
        try:
            org_id = getattr(g, "current_org_id", None)
            if org_id is None:
                return response

            from flask_login import current_user
            from app.services.usage_metering_service import UsageMeteringService

            user_id = current_user.id if current_user.is_authenticated else None

            # Detect successful login (2xx redirect from a login endpoint)
            if (
                request.endpoint in _LOGIN_ENDPOINTS
                and request.method == "POST"
                and response.status_code in (200, 302)
            ):
                UsageMeteringService.record(
                    org_id=org_id,
                    user_id=user_id,
                    event_type="user_login",
                )

            _record_export(response, org_id, user_id)

        except Exception as exc:  # noqa: BLE001
            logger.warning("install_usage_tracking after_request silenced: %s", exc)

        return response
