"""
UsageMeteringService — non-blocking event recording, monthly summaries, and seat enforcement.

Design contract:
- record() MUST NEVER raise.  All exceptions are swallowed internally and logged
  at WARNING level so that a metering failure never disrupts the main request.
- All other methods may raise normally (they are called in non-critical paths or
  explicitly to enforce limits).
"""

import logging
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class SeatLimitExceededError(Exception):
    """Raised by enforce_seat_limit() when the organization is over its user quota."""

    def __init__(self, org_id: int, used: int, purchased: int):
        self.org_id = org_id
        self.used = used
        self.purchased = purchased
        super().__init__(
            f"Organization {org_id} has used {used} of {purchased} seats."
        )


class UsageMeteringService:
    """Service for recording usage events and enforcing seat-count limits."""

    # ------------------------------------------------------------------ #
    # Event recording                                                      #
    # ------------------------------------------------------------------ #

    @staticmethod
    def record(
        org_id: int,
        user_id: Optional[int],
        event_type: str,
        resource_type: Optional[str] = None,
        resource_id: Optional[int] = None,
        metadata: Optional[Dict] = None,
    ) -> None:
        """Insert a UsageEvent row.

        This method is intentionally non-blocking: any exception is caught,
        logged, and silently discarded so that metering failures never break
        the main request flow.
        """
        try:
            from app.extensions import db
            from app.models.usage_event import UsageEvent

            event = UsageEvent(
                organization_id=org_id,
                user_id=user_id,
                event_type=event_type,
                resource_type=resource_type,
                resource_id=resource_id,
                metadata=metadata or {},
                recorded_at=datetime.utcnow(),
            )
            db.session.add(event)
            db.session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "UsageMeteringService.record() silenced exception "
                "(org=%s event=%s): %s",
                org_id,
                event_type,
                exc,
            )
            try:
                from app.extensions import db as _db
                _db.session.rollback()
            except Exception as exc:
                logger.debug("suppressed error in UsageMeteringService.record (app/services/usage_metering_service.py): %s", exc)

    # ------------------------------------------------------------------ #
    # Aggregation                                                          #
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_monthly_summary(org_id: int, year: int, month: int) -> Dict[str, int]:
        """Return {event_type: count} for every event type recorded in the given month."""
        import calendar
        from sqlalchemy import func
        from app.extensions import db
        from app.models.usage_event import UsageEvent

        # First and last instant of the requested month
        first_day = datetime(year, month, 1)
        last_day_num = calendar.monthrange(year, month)[1]
        last_day = datetime(year, month, last_day_num, 23, 59, 59)

        rows = (
            db.session.query(UsageEvent.event_type, func.count(UsageEvent.id))
            .filter(
                UsageEvent.organization_id == org_id,
                UsageEvent.recorded_at >= first_day,
                UsageEvent.recorded_at <= last_day,
            )
            .group_by(UsageEvent.event_type)
            .all()
        )
        return {event_type: count for event_type, count in rows}

    # ------------------------------------------------------------------ #
    # Seat enforcement                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_seat_usage(org_id: int) -> Dict:
        """Return {used, purchased, limit_reached} for the organization.

        Read from the plan limits (app/services/billing_plans.py) so this and
        the billing page give the same answer. ``purchased`` is None when the
        plan has no limit on people.
        """
        from app.services.billing_plans import user_limit_status

        status = user_limit_status(org_id)
        return {
            "used": status["used"],
            "purchased": status["limit"],
            "limit_reached": status["limit_reached"],
        }

    @staticmethod
    def check_seat_limit(org_id: int) -> bool:
        """Return True if the org is within its seat limit, False if over."""
        usage = UsageMeteringService.get_seat_usage(org_id)
        return not usage["limit_reached"]

    @staticmethod
    def enforce_seat_limit(org_id: int) -> None:
        """Raise SeatLimitExceededError if the org is over its seat limit."""
        usage = UsageMeteringService.get_seat_usage(org_id)
        if usage["limit_reached"]:
            raise SeatLimitExceededError(
                org_id=org_id,
                used=usage["used"],
                purchased=usage["purchased"],
            )

