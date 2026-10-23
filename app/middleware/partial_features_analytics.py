"""
Usage analytics middleware — page views on module-directory endpoints.

Writes one `usage_analytics` row per request whose endpoint is one this app's
module directory knows about (app/modules/modules_directory/routes.py), keyed
by the endpoint name. No personal data is stored: `UsageAnalytics.track_event`
is called without a `request` argument here, so `user_agent`, `ip_address` and
`referrer` stay `None` on every event this middleware writes. A signed-in user
who has turned analytics off for themselves (Settings > Enable Analytics) is
not tracked.

`ENABLE_USAGE_ANALYTICS` is owned by config.py; this module only reads it.
"""
import time
import uuid
from flask import current_app, g, request, session
from flask_login import current_user

from app.models.usage_analytics import UsageAnalytics


class PartialFeaturesAnalytics:
    """Writes one page-view event per module-directory endpoint request."""

    def __init__(self, app=None):
        self.app = app
        self._directory_endpoints = frozenset()
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        """Register the before/after/teardown hooks with the Flask app, and
        build the endpoint set once, here, rather than on every request."""
        self._directory_endpoints = self._compute_directory_endpoints()
        app.before_request(self._before_request)
        app.after_request(self._after_request)
        app.teardown_request(self._teardown_request)

    def _compute_directory_endpoints(self):
        """The set of endpoint names this middleware writes an event for:
        every module-directory link, plus the aliases the directory
        deliberately hides (`_NOT_RENDERED`) — a folded or not-rendered
        surface is still reachable by a direct request, and its count still
        matters for a retirement decision. Imported from the directory
        module, not copied, so it stays the one place this set is
        maintained. Built once, at install time: the directory's contents
        are fixed Python module state (SIDEBAR_ZONES / _MORE_TOOLS), not
        something that changes between requests within one running process."""
        from app.modules.modules_directory.routes import (
            _NOT_RENDERED,
            all_module_links,
        )

        endpoints = {link["endpoint"] for link in all_module_links()}
        endpoints.update(_NOT_RENDERED.keys())
        return frozenset(endpoints)

    def _opted_out(self):
        """True when the signed-in user has turned analytics off for
        themselves. Anonymous requests are never opted out — there is no
        per-user preference to read for them."""
        if not current_user.is_authenticated:
            return False
        return bool(current_user.get_notification_preference("analytics_opt_out"))

    def _before_request(self):
        """Note which directory endpoint this request is for, and start a
        timer. No write and no session write happen here: an anonymous
        visitor, or a request that turns out to be denied, must never cause
        a row or a session cookie before anyone has consented to anything
        (`_eligible` below, checked once the response exists, is what
        decides whether a write happens at all)."""
        if not current_app.config.get('ENABLE_USAGE_ANALYTICS', False):
            return

        # Skip static files — no analytics needed
        if request.path.startswith("/static/") or request.path == "/favicon.ico":
            return

        # request.environ has no 'REQUEST_TIME' key (that is not a WSGI
        # standard key Flask/Werkzeug populate); reading it always returned
        # the 0 default, so the "elapsed" time below was actually
        # time.time() - 0, i.e. the epoch time in milliseconds, not a
        # duration. time.time() captured here is a real start time.
        g.analytics_start_time = time.time()
        g.analytics_feature_name = (
            request.endpoint if request.endpoint in self._directory_endpoints else None
        )

    def _eligible(self, status_code):
        """True only for a request this tracker should record: a directory
        endpoint, a signed-in user (an anonymous request is not usage of the
        product by anyone who could have consented to being counted), a
        response that was not denied or an error (a 403/404/5xx is not a
        successful page view), the flag on, and no opt-out."""
        if not current_app.config.get('ENABLE_USAGE_ANALYTICS', False):
            return False
        if not g.get('analytics_feature_name'):
            return False
        if not current_user.is_authenticated:
            return False
        if status_code >= 400:
            return False
        return not self._opted_out()

    def _session_id(self):
        """The analytics session id, minted only at the moment a write is
        about to happen — never for a request that will not be recorded, so
        an anonymous or denied request never gets this cookie key set."""
        if 'analytics_session_id' not in session:
            session['analytics_session_id'] = str(uuid.uuid4())
        return session['analytics_session_id']

    def _after_request(self, response):
        """The single place this middleware writes: at most one event, and
        at most one `track_event` call (so at most one commit), per
        request — a page view for a read, a feature interaction for a
        successful write."""
        if not self._eligible(response.status_code):
            return response

        event_type = (
            'feature_interaction'
            if request.method in ('POST', 'PUT', 'PATCH', 'DELETE')
            else 'page_view'
        )
        metadata = None
        if event_type == 'feature_interaction':
            metadata = {
                'method': request.method,
                'status_code': response.status_code,
                'response_time_ms': self._calculate_response_time(),
            }

        UsageAnalytics.track_event(
            event_type=event_type,
            feature_name=g.analytics_feature_name,
            route_path=request.path,
            user_id=current_user.id,
            session_id=self._session_id(),
            event_metadata=metadata,
        )

        return response

    def _teardown_request(self, exception):
        """Track an unhandled error on a directory endpoint — only when a
        response was never produced (a real exception, not a 4xx/5xx that an
        error handler turned into a response; `_after_request` already
        decided not to write for those). Same eligibility as any other
        event: signed-in, opted in, flag on."""
        if not exception:
            return
        if not current_app.config.get('ENABLE_USAGE_ANALYTICS', False):
            return
        if not g.get('analytics_feature_name'):
            return
        if not current_user.is_authenticated:
            return
        if self._opted_out():
            return

        UsageAnalytics.track_event(
            event_type='error_occurred',
            feature_name=g.analytics_feature_name,
            route_path=request.path,
            user_id=current_user.id,
            session_id=self._session_id(),
            event_metadata={
                'error_type': type(exception).__name__,
                'error_message': str(exception),
                'method': request.method
            },
        )

    def _calculate_response_time(self):
        """Calculate response time in milliseconds."""
        if hasattr(g, 'analytics_start_time'):
            return int((time.time() - g.analytics_start_time) * 1000)
        return 0
