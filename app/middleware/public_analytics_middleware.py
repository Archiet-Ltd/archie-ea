"""First-party, cookieless pageview counting for public marketing pages.

Registers an ``after_request`` hook that logs a ``page_view`` event (see
app/services/public_analytics_service.py) for a GET to one of Entelim's
public marketing page endpoints. Separate from
app/middleware/analytics_middleware.py, which sends an authenticated user's
pageviews to PostHog (a no-op unless POSTHOG_API_KEY is set) -- that hook
never fires for an anonymous marketing-page visitor, which is exactly who
this one is for.
"""

from __future__ import annotations

import logging

from flask import request

logger = logging.getLogger(__name__)

# Every endpoint that renders a public marketing page. Kept as an explicit
# allow-list, not "every HTML response", so this never starts counting
# dashboard/application screens as marketing pageviews.
PUBLIC_PAGE_ENDPOINTS = frozenset(
    {
        "main.index",
        "main.public_vision",
        "main.public_module",
        "main.public_use_case",
        "main.public_comparison",
        "main.public_dogfood",
        "main.public_site_page",
        "main.public_legal_page",
    }
)


def install_public_pageview_tracking(app) -> None:
    """Register the first-party pageview after_request hook on *app*."""

    @app.after_request
    def _track_public_pageview(response):
        try:
            if request.method != "GET":
                return response
            if request.endpoint not in PUBLIC_PAGE_ENDPOINTS:
                return response
            content_type = response.content_type or ""
            if "text/html" not in content_type:
                return response
            if response.status_code != 200:
                return response

            from app.services.public_analytics_service import log_page_view

            log_page_view(request.path)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "public pageview tracking failed (non-blocking): %s", exc
            )

        return response
