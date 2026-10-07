"""PublicVisitorEvent — first-party, cookieless analytics for public pages.

Counts how many people see a public marketing page and a small set of
funnel events (sign-up started/completed, a pricing-plan click, an offer
enquiry submitted) in Entelim's own database only.

No organisation, no user link: like WaitlistSignup and ProductInquiry, a
marketing-page visitor has neither. Unlike those two, there is also no
email and no name here -- nothing identifying is ever stored. ``visitor_hash``
is a one-way, salted, daily-rotating digest of the visitor's IP address and
user agent (see app/services/visitor_hash.py); it is used only to group a
handful of events from what is very likely the same visitor close together
in time (e.g. sign-up started -> sign-up completed), and it changes every
day so it cannot be used to build a longer-term profile. The raw IP address
and user agent are never stored anywhere.

Separate from:
- app/models/event_log.py (EventLogRecord) -- the tenant-scoped business
  event stream for signed-in organisations. A marketing visitor has no
  organisation, so that model does not fit.
- app/services/analytics_service.py (PostHog) -- third-party, signed-in
  product usage, no-op unless POSTHOG_API_KEY is set.
"""

from __future__ import annotations

from app import db

EVENT_TYPES = (
    "page_view",
    "signup_started",
    "signup_completed",
    "pricing_plan_click",
    "offer_enquiry_submitted",
)


class PublicVisitorEvent(db.Model):
    __tablename__ = "public_visitor_events"

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    event_type = db.Column(db.String(40), nullable=False, index=True)
    # Which page (page_view) -- the request path, e.g. "/pricing".
    path = db.Column(db.String(255), nullable=True)
    # Which plan (pricing_plan_click), e.g. "startup", "team", "community".
    plan = db.Column(db.String(40), nullable=True)
    # Which offer (offer_enquiry_submitted) -- same value as
    # ProductInquiry.offer for the same submission.
    offer = db.Column(db.String(80), nullable=True)
    # One-way SHA-256 digest of (ip, user agent, server secret, UTC day).
    # Never the ip or user agent themselves -- see app/services/visitor_hash.py.
    visitor_hash = db.Column(db.String(64), nullable=False, index=True)
    # The UTC calendar day the hash was computed for. Stored so events can be
    # correlated within the window the hash covers; this is a date, not a
    # timestamp of the visit, and on its own identifies nobody.
    visitor_day = db.Column(db.Date, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now()
    )

    __table_args__ = (
        db.CheckConstraint(
            "event_type IN ('page_view','signup_started','signup_completed',"
            "'pricing_plan_click','offer_enquiry_submitted')",
            name="ck_public_visitor_events_event_type",
        ),
        db.Index(
            "ix_public_visitor_events_hash_day", "visitor_hash", "visitor_day"
        ),
    )

    def __repr__(self):
        return f"<PublicVisitorEvent {self.event_type}>"


__all__ = ["PublicVisitorEvent", "EVENT_TYPES"]
