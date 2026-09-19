"""
My Applications Services (NS-012, NS-013)

Business logic for application manager features including
filtered application lists and ownership management.

ADR Reference: docs/adr/0011-application-manager-persona.md
"""

from typing import Dict, List, Optional, Tuple

from sqlalchemy import and_, or_

from app.models.application_owner import ApplicationOwner
from app.models.application_portfolio import ApplicationComponent


def _ownership_rows(
    user_id: int, ownership_type: Optional[str] = None
) -> Tuple[List[ApplicationOwner], Dict[int, ApplicationComponent]]:
    """The one definition of "an application this user owns".

    An application is owned by a user when at least one ownership row names that
    user for it and the application itself is visible in the user's tenant. One
    application can carry several rows for the same user (primary and technical,
    say); it is still one application. Every number and every list on the My
    Applications screens is derived from this function, so no two of them can
    disagree; has_assigned_owner() below reads the same store for the portfolio.

    Returns (ownership rows whose application is visible, {id: application}).
    """
    # tenant-scoping-ok: self-lookup, filtered by the authenticated user's own id.
    query = ApplicationOwner.query.filter(ApplicationOwner.user_id == user_id)
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


def has_assigned_owner():
    """SQL predicate over ApplicationComponent: someone is on record as its owner.

    True when a business owner is recorded on the application or when at least one
    application-manager assignment exists for it. The portfolio's "assigned owner"
    count uses this, so it agrees with the ownership rows an application manager
    sees on My Applications.
    """
    return or_(
        and_(
            ApplicationComponent.business_owner.isnot(None),
            ApplicationComponent.business_owner != "",
        ),
        ApplicationComponent.owners.any(),
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
        search_term = f"%{search}%"
        app_query = app_query.filter(
            or_(
                ApplicationComponent.name.ilike(search_term),
                ApplicationComponent.description.ilike(search_term),
            )
        )

    app_query = app_query.order_by(ApplicationComponent.name.asc())

    total = app_query.count()
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


def get_ownership_summary(user_id: int) -> Dict[str, int]:
    """Number of applications the user owns, in total and under each ownership type.

    Counts applications, not ownership rows: an application held under two types
    counts once in "total" and once under each type. Each type is therefore never
    larger than "total".
    """
    rows, visible = _ownership_rows(user_id)

    summary = {"total": len(visible)}
    for ownership_type in ApplicationOwner.OWNERSHIP_TYPES:
        summary[ownership_type] = len(
            {r.application_id for r in rows if r.ownership_type == ownership_type}
        )
    return summary


def get_application_health_summary(user_id: int) -> Dict[str, any]:
    """Get health summary for user's applications."""
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
        # Determine health based on available fields
        # This is a simplified health check - real implementation would use more factors
        lifecycle = getattr(app, 'lifecycle_status', None)
        if lifecycle in ['active', 'production']:
            summary["healthy"] += 1
        elif lifecycle in ['sunset', 'retiring']:
            summary["at_risk"] += 1
        elif lifecycle in ['decommissioned', 'retired']:
            summary["critical"] += 1
        else:
            summary["unknown"] += 1

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
