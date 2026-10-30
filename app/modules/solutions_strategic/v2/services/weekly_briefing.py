"""The weekly architecture briefing.

One page for the chief architect's week: what changed, which decisions are
due, which risks are rising and how each solution conforms. Every item is read
from the model on request and carries the date it happened and a link to its
record. Nothing is stored and nothing is generated.

The signed-in organisation is applied by the tenant filter, and explicitly
wherever a query is not covered by it.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from app import db
from app.utils.locale_format import fmt_date, fmt_date_range

#: Items shown in a section before it points at the page holding the rest.
ITEMS_PER_SECTION = 5

#: How many weeks back the list of earlier weeks looks.
EARLIER_WEEKS_WINDOW = 12

#: How many earlier weeks are listed.
EARLIER_WEEKS_SHOWN = 8

#: A review due within this many days of its limit counts as due.
DUE_SOON_DAYS = 7


def week_start_for(day: date) -> date:
    """The Monday of the week holding ``day``."""
    return day - timedelta(days=day.weekday())


def parse_week(value: Optional[str]) -> Optional[date]:
    """Read a ``YYYY-MM-DD`` week from the address; None when it is not a date."""
    if not value:
        return None
    try:
        return week_start_for(datetime.strptime(value, "%Y-%m-%d").date())
    except ValueError:
        return None


def _plural(count: int, singular: str, plural: Optional[str] = None) -> str:
    word = singular if count == 1 else (plural or singular + "s")
    return f"{count} {word}"


def _section(key, heading, items, empty_text, page_label, page_href, more_label):
    shown = items[:ITEMS_PER_SECTION]
    hidden = len(items) - len(shown)
    return {
        "key": key,
        "heading": heading,
        "entries": shown,
        "total": len(items),
        "empty_text": empty_text,
        "page_label": page_label,
        "page_href": page_href,
        "more_text": (
            f"and {hidden} more on {more_label}" if hidden > 0 else None
        ),
    }


def _changes(org_id, start: datetime, end: datetime) -> List[Dict[str, Any]]:
    """Elements added or changed in the week, newest first."""
    from app.models.archimate_core import ArchiMateElement
    from app.modules.intelligence.services.history_service import HistoryService

    result = HistoryService(db.session, org_id).difference(start, end)
    latest: Dict[int, Any] = {}
    for change in result.changes:
        if change.table_name != "archimate_elements" or change.action == "delete":
            continue
        if not (start <= change.valid_from < end):
            continue
        known = latest.get(change.record_id)
        if known is None or change.valid_from > known.valid_from:
            latest[change.record_id] = change
    if not latest:
        return []
    existing = {
        row.id
        for row in db.session.execute(
            db.select(ArchiMateElement.id).where(
                ArchiMateElement.id.in_(list(latest)),
                ArchiMateElement.organization_id == org_id,
            )
        )
    }
    items = []
    for record_id, change in latest.items():
        if record_id not in existing:
            continue
        snapshot = change.snapshot or {}
        verb = "Added" if change.action == "insert" else "Changed"
        kind = snapshot.get("type") or "element"
        items.append(
            {
                "title": snapshot.get("name") or kind,
                "detail": f"{verb} {kind}",
                "date": change.valid_from.date(),
                "sort": change.valid_from,
                "href": f"/architecture/elements/{record_id}",
            }
        )
    items.sort(key=lambda i: i["sort"], reverse=True)
    return items


def _decisions_due(now: datetime):
    """Reviews past their limit, then those about to pass it.

    Returns the items and how many of them are past their limit."""
    from app.models.architecture_review_board import ARBReviewItem
    from app.modules.architecture.services.arb_overdue import (
        OVERDUE_LIMIT_DAYS,
        overdue_reviews,
    )

    items = []
    overdue_ids = set()
    overdue = overdue_reviews(now)
    for entry in overdue:
        review = entry["review"]
        overdue_ids.add(review.id)
        late = entry["days_overdue"] - entry["threshold"]
        items.append(
            {
                "title": f"{review.review_number} · {review.title}",
                "detail": (
                    f"Submitted, {_plural(late, 'day')} past its "
                    f"{entry['threshold']}-day limit"
                ),
                "date": review.submitted_at.date(),
                "href": f"/arb/reviews/{review.id}",
            }
        )
    open_reviews = (
        ARBReviewItem.query.filter(
            ARBReviewItem.status.in_(["submitted", "under_review"]),
            ARBReviewItem.submitted_at.isnot(None),
        )
        .order_by(ARBReviewItem.submitted_at.asc())
        .all()
    )
    for review in open_reviews:
        if review.id in overdue_ids:
            continue
        limit = OVERDUE_LIMIT_DAYS.get(review.priority)
        if limit is None:
            continue
        left = limit - (now - review.submitted_at).days
        if left <= DUE_SOON_DAYS:
            items.append(
                {
                    "title": f"{review.review_number} · {review.title}",
                    "detail": (
                        f"Due for a decision in {_plural(max(left, 0), 'day')}"
                    ),
                    "date": review.submitted_at.date(),
                    "href": f"/arb/reviews/{review.id}",
                }
            )
    return items, len(overdue)


def _decisions_taken(org_id, start: datetime, end: datetime) -> List[Dict[str, Any]]:
    from app.models.architecture_review_board import ARBReviewItem

    reviews = (
        ARBReviewItem.query.filter(
            ARBReviewItem.organization_id == org_id,
            ARBReviewItem.decision_date.isnot(None),
            ARBReviewItem.decision_date >= start,
            ARBReviewItem.decision_date < end,
        )
        .order_by(ARBReviewItem.decision_date.desc())
        .all()
    )
    return [
        {
            "title": f"{review.review_number} · {review.title}",
            "detail": "Decision: " + (review.decision or "").replace("_", " "),
            "date": review.decision_date.date(),
            "href": f"/arb/reviews/{review.id}",
        }
        for review in reviews
    ]


def _risks_rising(org_id, start: datetime, end: datetime) -> List[Dict[str, Any]]:
    """Risks whose latest score this week is higher than the one before it."""
    from app.models.risk import Risk
    from app.models.risk_score_history import RiskScoreHistory

    rows = (
        RiskScoreHistory.query.filter(RiskScoreHistory.organization_id == org_id)
        .order_by(RiskScoreHistory.recorded_at.asc(), RiskScoreHistory.id.asc())
        .all()
    )
    series = defaultdict(list)
    for row in rows:
        if row.recorded_at < end:
            series[(row.risk_id, row.score_kind)].append(row)
    rising: Dict[int, Dict[str, Any]] = {}
    for (risk_id, kind), history in series.items():
        if len(history) < 2:
            continue
        last, before = history[-1], history[-2]
        if not (start <= last.recorded_at < end):
            continue
        now_score = last.likelihood * last.impact
        was_score = before.likelihood * before.impact
        if now_score <= was_score:
            continue
        known = rising.get(risk_id)
        if known is None or now_score - was_score > known["rise"]:
            rising[risk_id] = {
                "rise": now_score - was_score,
                "kind": kind,
                "was": was_score,
                "now": now_score,
                "at": last.recorded_at,
            }
    if not rising:
        return []
    risks = {
        risk.id: risk
        for risk in Risk.query.filter(
            Risk.id.in_(list(rising)), Risk.organization_id == org_id
        ).all()
    }
    items = []
    for risk_id, movement in rising.items():
        risk = risks.get(risk_id)
        if risk is None:
            continue
        items.append(
            {
                "title": risk.title,
                "detail": (
                    f"{movement['kind'].title()} risk score up from "
                    f"{movement['was']} to {movement['now']}"
                ),
                "date": movement["at"].date(),
                "sort": movement["at"],
                "href": f"/risks/{risk.id}",
            }
        )
    items.sort(key=lambda i: i["sort"], reverse=True)
    return items


def _conformance(today: date) -> List[Dict[str, Any]]:
    """Each assessed solution's conformance, lowest first."""
    from app.models.solution_models import Solution
    from app.modules.solutions_strategic.v2.services.conformance_reviewer import (
        ConformanceReviewer,
    )

    solutions = (
        Solution.query.filter(
            ~Solution.name.like("ZZ %"), ~Solution.name.like("Untitled Solution%")
        )
        .order_by(Solution.id.desc())
        .limit(60)
        .all()
    )
    items = []
    for solution in solutions:
        try:
            review = ConformanceReviewer.review(solution.id)
        except Exception:  # noqa: BLE001 - an unassessable solution is left out
            continue
        if not review.get("success") or review.get("score") is None:
            continue
        flagged = review.get("flagged", 0)
        items.append(
            {
                "title": solution.name,
                "detail": (
                    f"Conformance {review['score']} out of 100 · "
                    + (
                        f"{_plural(flagged, 'issue')} to resolve"
                        if flagged
                        else "no issues to resolve"
                    )
                ),
                "date": today,
                "score": review["score"],
                "flagged": flagged,
                "href": f"/solutions/{solution.id}/review-packet",
            }
        )
    items.sort(key=lambda i: (i["score"], i["title"]))
    return items


def _active_weeks(org_id, current: date) -> List[date]:
    """Earlier weeks in which something changed, newest first."""
    from app.models.architecture_review_board import ARBReviewItem
    from app.models.entity_history import EntityHistory
    from app.models.risk_score_history import RiskScoreHistory

    since = datetime.combine(current, datetime.min.time()) - timedelta(
        weeks=EARLIER_WEEKS_WINDOW
    )
    before = datetime.combine(current, datetime.min.time())
    days = set()
    for (stamp,) in db.session.execute(
        db.select(EntityHistory.valid_from).where(
            EntityHistory.organization_id == org_id,
            EntityHistory.table_name == "archimate_elements",
            EntityHistory.valid_from >= since,
            EntityHistory.valid_from < before,
        )
    ):
        days.add(stamp.date())
    for (stamp,) in db.session.execute(
        db.select(RiskScoreHistory.recorded_at).where(
            RiskScoreHistory.organization_id == org_id,
            RiskScoreHistory.recorded_at >= since,
            RiskScoreHistory.recorded_at < before,
        )
    ):
        days.add(stamp.date())
    for (stamp,) in db.session.execute(
        db.select(ARBReviewItem.decision_date).where(
            ARBReviewItem.organization_id == org_id,
            ARBReviewItem.decision_date.isnot(None),
            ARBReviewItem.decision_date >= since,
            ARBReviewItem.decision_date < before,
        )
    ):
        days.add(stamp.date())
    weeks = sorted({week_start_for(d) for d in days}, reverse=True)
    return weeks[:EARLIER_WEEKS_SHOWN]


def build(org_id: int, week: Optional[date] = None, now: Optional[datetime] = None):
    """The briefing for ``week`` (default: the current week)."""
    now = now or datetime.utcnow()
    current = week_start_for(now.date())
    week = min(week or current, current)
    is_current = week == current
    start = datetime.combine(week, datetime.min.time())
    end = start + timedelta(days=7)

    changes = _changes(org_id, start, end)
    risks = _risks_rising(org_id, start, end)
    sections = [
        _section(
            "changes",
            "What changed",
            changes,
            "Nothing was added or changed in the model this week.",
            "the change history",
            "/intelligence/history/changes",
            "the change history",
        )
    ]
    if is_current:
        decisions, overdue = _decisions_due(now)
        sections.append(
            _section(
                "decisions",
                "Decisions due",
                decisions,
                "No review is due or past its limit.",
                "the review board",
                "/arb/",
                "the review board",
            )
        )
        conformance = _conformance(now.date())
        sections.append(
            _section(
                "risks",
                "Risks rising",
                risks,
                "No risk score rose this week.",
                "the risk register",
                "/risks/",
                "the risk register",
            )
        )
        sections.append(
            _section(
                "conformance",
                "Design conformance",
                conformance,
                "No solution has enough modelled to assess yet.",
                "the solutions list",
                "/solutions/",
                "the solutions list",
            )
        )
        with_issues = sum(1 for i in conformance if i["flagged"])
        headline = (
            f"This week: {_plural(len(changes), 'change')} to the model, "
            f"{_plural(overdue, 'review')} past the time limit, "
            f"{_plural(len(risks), 'risk')} rising and "
            f"{_plural(with_issues, 'solution')} with conformance issues."
        )
    else:
        taken = _decisions_taken(org_id, start, end)
        sections.append(
            _section(
                "decisions",
                "Decisions taken",
                taken,
                "No review was decided this week.",
                "the review board",
                "/arb/",
                "the review board",
            )
        )
        sections.append(
            _section(
                "risks",
                "Risks rising",
                risks,
                "No risk score rose this week.",
                "the risk register",
                "/risks/",
                "the risk register",
            )
        )
        headline = (
            f"That week: {_plural(len(changes), 'change')} to the model, "
            f"{_plural(len(taken), 'review decision')} and "
            f"{_plural(len(risks), 'risk')} rising."
        )

    last_day = end - timedelta(days=1)
    return {
        "week": week,
        "is_current": is_current,
        "label": fmt_date_range(week, last_day),
        "headline": headline,
        "sections": sections,
        "earlier": [
            {"week": w, "label": f"Week of {fmt_date(w)}"}
            for w in _active_weeks(org_id, current)
        ],
    }
