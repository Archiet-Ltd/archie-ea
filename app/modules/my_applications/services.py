"""
My Applications Services (NS-012, NS-013)

Business logic for application manager features including
filtered application lists and ownership management.

ADR Reference: docs/adr/0011-application-manager-persona.md
"""

from typing import Dict, List, Optional, Tuple

from sqlalchemy import and_, or_, select

from app.models.application_owner import ApplicationOwner
from app.models.application_portfolio import APPLICATION_HEALTH_STATUSES, ApplicationComponent
from app.models.user import User


def _ownership_rows(
    user_id: int, ownership_type: Optional[str] = None
) -> Tuple[List[ApplicationOwner], Dict[int, ApplicationComponent]]:
    """The one definition of "an application this user owns".

    An application is owned by a user when at least one ownership row names that
    user for it, the row belongs to the same organisation as the user, and the
    application itself is visible in the user's tenant. One application can carry
    several rows for the same user (primary and technical, say); it is still one
    application. Every number and every list on the My Applications screens is
    derived from this function, so no two of them can disagree; has_assigned_owner()
    below applies the same organisation rule for the portfolio.

    Returns (ownership rows whose application is visible, {id: application}).
    """
    # tenant-scoping-ok: self-lookup, filtered by the authenticated user's own id.
    query = ApplicationOwner.query.join(User, User.id == ApplicationOwner.user_id).filter(
        ApplicationOwner.user_id == user_id,
        ApplicationOwner.organization_id == User.organization_id,
    )
    if ownership_type:
        query = query.filter(ApplicationOwner.ownership_type == ownership_type)
    rows = query.all()

    app_ids = {r.application_id for r in rows}
    if not app_ids:
        return [], {}

    visible = {
        a.id: a
        for a in ApplicationComponent.query.filter(ApplicationComponent.id.in_(list(app_ids))).all()
    }
    return [r for r in rows if r.application_id in visible], visible


def get_owned_applications(user_id: int) -> List[ApplicationComponent]:
    """Applications owned by the user, one entry each, ordered by name."""
    _, visible = _ownership_rows(user_id)
    return sorted(visible.values(), key=lambda a: (a.name or "").lower())


def has_assigned_owner(organization_id: int):
    """SQL predicate over ApplicationComponent: someone is named as its owner.

    True when a business owner is recorded on the application, or when an
    application manager of the given organisation is assigned to it. An assignment
    only counts when both the assignment and the assigned user belong to that
    organisation, so another organisation's rows never make an application look
    owned. The portfolio's owner count uses this, so its assignment half is the
    same ownership the My Applications screens count for a user.
    """
    assigned_here = (
        select(ApplicationOwner.id)
        .join(User, User.id == ApplicationOwner.user_id)
        .where(
            ApplicationOwner.application_id == ApplicationComponent.id,
            ApplicationOwner.organization_id == organization_id,
            User.organization_id == organization_id,
        )
        .exists()
    )
    return or_(
        and_(
            ApplicationComponent.business_owner.isnot(None),
            ApplicationComponent.business_owner != "",
        ),
        assigned_here,
    )


def _search_clause(search: str):
    """Match applications whose name or description contains the search text.

    The text is matched literally: a backslash, percent sign or underscore in it
    stands for itself rather than acting as a pattern character.
    """
    escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    term = f"%{escaped}%"
    return or_(
        ApplicationComponent.name.ilike(term, escape="\\"),
        ApplicationComponent.description.ilike(term, escape="\\"),
    )


def get_user_applications(
    user_id: int,
    ownership_type: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    per_page: int = 20,
) -> Tuple[List[dict], int]:
    """
    Get applications owned by a specific user.

    Returns: (applications with ownership info, total_count)
    """
    ownership_records, _ = _ownership_rows(user_id, ownership_type)
    app_ids = {o.application_id for o in ownership_records}

    if not app_ids:
        return [], 0

    # Get applications
    app_query = ApplicationComponent.query.filter(ApplicationComponent.id.in_(list(app_ids)))

    if search:
        app_query = app_query.filter(_search_clause(search))

    app_query = app_query.order_by(ApplicationComponent.name.asc())

    total = app_query.count()
    # A page past the end is served as the last page; the offset is only ever
    # computed from a page that exists.
    last_page = max(1, -(-total // per_page))
    page = min(max(page, 1), last_page)
    apps = app_query.offset((page - 1) * per_page).limit(per_page).all()

    # Build ownership lookup. An application held under several types is shown
    # under the strongest one (primary before backup, technical, business), so the
    # card and the Primary badge do not depend on row order.
    rank = {t: i for i, t in enumerate(ApplicationOwner.OWNERSHIP_TYPES)}
    ownership_map = {}
    for o in sorted(ownership_records, key=lambda o: rank.get(o.ownership_type, len(rank))):
        ownership_map.setdefault(o.application_id, o)

    # Combine app data with ownership info
    result = []
    for app in apps:
        ownership = ownership_map.get(app.id)
        result.append({
            "application": app,
            "ownership_type": ownership.ownership_type if ownership else None,
            "is_primary": ownership.is_primary if ownership else False,
        })

    return result, total


def get_ownership_summary(user_id: int, search: Optional[str] = None) -> Dict[str, int]:
    """Number of applications the user owns, in total and under each ownership type.

    Counts applications, not ownership rows: an application held under two types
    counts once in "total" and once under each type. Each type is therefore never
    larger than "total". With a search, only applications matching it are counted,
    so the figures agree with the rows the list shows for that search.
    """
    rows, visible = _ownership_rows(user_id)

    if search and visible:
        matching = {
            app_id
            for (app_id,) in ApplicationComponent.query.with_entities(ApplicationComponent.id)
            .filter(ApplicationComponent.id.in_(list(visible)), _search_clause(search))
            .all()
        }
        visible = {app_id: a for app_id, a in visible.items() if app_id in matching}
        rows = [r for r in rows if r.application_id in visible]

    summary = {"total": len(visible)}
    for ownership_type in ApplicationOwner.OWNERSHIP_TYPES:
        summary[ownership_type] = len(
            {r.application_id for r in rows if r.ownership_type == ownership_type}
        )
    return summary


def application_health_status(application) -> str:
    """The health of an application: the status recorded on it.

    One of ``healthy``, ``at_risk`` or ``critical``, or ``unknown`` when nothing
    is recorded (or the recorded value is outside that vocabulary). The health
    tiles and the grouped list on the health page both read it, so a count and
    the rows under it cannot disagree. Lifecycle stage is a different fact and is
    never read as health.
    """
    status = getattr(application, "health_status", None)
    return status if status in APPLICATION_HEALTH_STATUSES else "unknown"


def get_application_health_summary(user_id: int) -> Dict[str, any]:
    """Count the user's applications by their recorded health status."""
    _, visible = _ownership_rows(user_id)
    apps = list(visible.values())

    summary = {
        "total": len(apps),
        "healthy": 0,
        "at_risk": 0,
        "critical": 0,
        "unknown": 0,
    }
    for app in apps:
        summary[application_health_status(app)] += 1

    return summary


def get_roadmap_impacts(user_id: int) -> List[dict]:
    """Get roadmap items affecting user's applications."""
    # Get the user's applications with roadmap-relevant fields
    apps = get_owned_applications(user_id)

    impacts = []
    for app in apps:
        # Check for sunset/retirement dates
        sunset_date = getattr(app, 'sunset_date', None)
        if sunset_date:
            impacts.append({
                "application": app,
                "impact_type": "sunset",
                "date": sunset_date,
                "description": f"{app.name} is scheduled for sunset",
            })

        # Check lifecycle status
        lifecycle = getattr(app, 'lifecycle_status', None)
        if lifecycle in ['sunset', 'retiring']:
            impacts.append({
                "application": app,
                "impact_type": "lifecycle_change",
                "date": None,
                "description": f"{app.name} is in {lifecycle} phase",
            })

    # Sort by date (None dates last)
    impacts.sort(key=lambda x: (x["date"] is None, x["date"]))

    return impacts[:20]  # Limit to 20 most relevant
