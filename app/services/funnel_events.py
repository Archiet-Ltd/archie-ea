"""Launch funnel counts: sign-ups, first answers shown, health-check requests.

Events go through the shared ``UsageEvent`` model. "Once per organisation" is a
check made here, not a database constraint, so repeatable events elsewhere
(sign-ins, tool calls) are unaffected.
"""

import logging

from sqlalchemy import func

from app.extensions import db
from app.models.usage_event import UsageEvent

logger = logging.getLogger(__name__)

FUNNEL_EVENT_TYPES = (
    UsageEvent.EVENT_ORGANISATION_SIGNED_UP,
    UsageEvent.EVENT_FIRST_ANSWER_SHOWN,
)


def record_once(organization_id, event_type):
    """Record ``event_type`` for the organisation unless one already exists.

    Runs inside the caller's transaction (a savepoint, so a failure here rolls
    back only this write) and never raises into the request. The caller commits.
    Returns True when an event was added.
    """
    if organization_id is None:
        return False
    try:
        with db.session.begin_nested():
            exists = db.session.query(UsageEvent.id).filter(
                UsageEvent.organization_id == organization_id,
                UsageEvent.event_type == event_type,
            ).first()
            if exists is not None:
                return False
            db.session.add(UsageEvent(organization_id=organization_id, event_type=event_type))
        return True
    except Exception:
        logger.exception("funnel event %s not recorded", event_type)
        return False


def _health_check_requests():
    """Stored health-check requests, anonymous visitors included (rows, not organisations)."""
    from app.models.product_inquiry import ProductInquiry

    return db.session.query(func.count(ProductInquiry.id)).filter(
        ProductInquiry.offer == "architecture_health_check"
    ).scalar() or 0


def funnel_counts():
    """Organisations with each of the first two events, plus all health-check requests."""
    rows = db.session.query(
        UsageEvent.event_type, func.count(func.distinct(UsageEvent.organization_id))
    ).filter(UsageEvent.event_type.in_(FUNNEL_EVENT_TYPES)).group_by(UsageEvent.event_type).all()
    found = dict(rows)
    return {
        "signed_up": found.get(UsageEvent.EVENT_ORGANISATION_SIGNED_UP, 0),
        "first_answer": found.get(UsageEvent.EVENT_FIRST_ANSWER_SHOWN, 0),
        "health_check": _health_check_requests(),
    }
