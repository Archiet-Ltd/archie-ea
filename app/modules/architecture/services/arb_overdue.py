"""Which Architecture Review Board reviews are past their time limit.

The review board dashboard and the weekly briefing both ask this question, so
they share this one answer: a review still waiting for a decision is overdue
once it has been open longer than the limit for its priority.
"""

from datetime import datetime, timedelta

from app.models.architecture_review_board import ARBReviewItem

#: Days a review may wait for a decision, by priority.
OVERDUE_LIMIT_DAYS = {"critical": 7, "high": 14, "medium": 21, "low": 30}

_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def overdue_reviews(now=None):
    """Return the signed-in organisation's overdue reviews, most urgent first.

    Each entry holds ``review``, ``days_overdue`` (days since submission) and
    ``threshold`` (the limit that applied).
    """
    now = now or datetime.utcnow()
    items = []
    for priority, limit_days in OVERDUE_LIMIT_DAYS.items():
        cutoff = now - timedelta(days=limit_days)
        reviews = (
            ARBReviewItem.query.filter(
                ARBReviewItem.status.in_(["submitted", "under_review"]),
                ARBReviewItem.priority == priority,
                ARBReviewItem.submitted_at.isnot(None),
                ARBReviewItem.submitted_at <= cutoff,
            )
            .order_by(ARBReviewItem.submitted_at.asc())
            .all()
        )
        for review in reviews:
            items.append(
                {
                    "review": review,
                    "days_overdue": (now - review.submitted_at).days,
                    "threshold": limit_days,
                }
            )
    items.sort(
        key=lambda x: (
            _PRIORITY_RANK.get(x["review"].priority, 4),
            -x["days_overdue"],
        )
    )
    return items
