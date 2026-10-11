"""First-party, cookieless analytics for Entelim's own public marketing pages.

Counts page views and a small set of funnel events -- sign-up started, sign-up
completed, a pricing-plan click, an offer enquiry submitted -- in Entelim's
own database. No third-party script, no cookie, no localStorage write, no
persistent identifier: see app/services/visitor_hash.py for how same-session
events are correlated without storing anything reversible to a visitor.

Deliberately separate from:
- app/services/analytics_service.py (PostHog) -- third-party, signed-in
  product usage, no-op unless POSTHOG_API_KEY is set.
- app/models/event_log.py (EventLogRecord) -- the tenant-scoped business
  event stream for signed-in organisations; a marketing-page visitor has no
  organisation yet, so that model does not fit.

Every function here is best-effort and non-blocking: a logging failure is
swallowed (after rollback) and never surfaces to the visitor or interrupts
the page/flow being measured, the same resilience pattern used by the
PostHog pageview hook and the SOC 2 audit listeners elsewhere in this app.
"""

from __future__ import annotations

import logging

from flask import current_app, has_request_context, request

logger = logging.getLogger(__name__)


def _visitor_hash() -> str:
    from app.services.visitor_hash import request_visitor_hash

    secret = (
        current_app.config.get("VISITOR_HASH_SECRET")
        or current_app.config.get("SECRET_KEY")
        or ""
    )
    return request_visitor_hash(request, secret)


def _record(event_type: str, *, path=None, plan=None, offer=None):
    """Write one event row. Returns the row, or ``None`` on no-op/failure."""
    if not has_request_context():
        return None

    from app import db
    from app.models.public_visitor_event import PublicVisitorEvent
    from app.services.visitor_hash import current_utc_day

    try:
        row = PublicVisitorEvent(
            event_type=event_type,
            path=path,
            plan=plan,
            offer=offer,
            visitor_hash=_visitor_hash(),
            visitor_day=current_utc_day(),
        )
        db.session.add(row)
        db.session.commit()
        return row
    except Exception:
        db.session.rollback()
        logger.debug(
            "public visitor event logging failed (non-blocking): %s",
            event_type,
            exc_info=True,
        )
        return None


def log_page_view(path: str):
    """A visitor viewed a public marketing page."""
    return _record("page_view", path=path)


def log_signup_started():
    """A visitor reached the registration form."""
    return _record("signup_started")


def log_signup_completed():
    """A visitor's account was created."""
    return _record("signup_completed")


def log_pricing_plan_click(plan: str):
    """A visitor clicked a pricing-plan CTA, from the pricing page or a
    module page's "Choose a plan" block."""
    return _record("pricing_plan_click", plan=plan)


def log_offer_enquiry_submitted(offer: str):
    """A visitor submitted an enquiry for a fixed-price offer."""
    return _record("offer_enquiry_submitted", offer=offer)


__all__ = [
    "log_page_view",
    "log_signup_started",
    "log_signup_completed",
    "log_pricing_plan_click",
    "log_offer_enquiry_submitted",
]
