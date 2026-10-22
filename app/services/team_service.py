"""Team service: manage teams (BusinessActor with actor_type='Team') and allocations.

A team is a BusinessActor with ``actor_type='Team'``. Allocations record the
share of a team's time spent on an application or technology element.
"""

import logging
from datetime import date
from typing import Optional

from app import db
from app.models.business_layer import BusinessActor
from app.models.team_allocation import TeamAllocation

logger = logging.getLogger(__name__)

# Valid element types for team allocations
ALLOCATION_ELEMENT_TYPES = frozenset({"application", "technology"})


class AllocationOverflow(ValueError):
    """The team's total allocated share would exceed 100 percent."""


class InvalidElementType(ValueError):
    """The element type is not valid for team allocations."""


def get_teams_for_organization(organization_id: int):
    """Return all teams (BusinessActor with actor_type='Team') in an organisation."""
    return (
        BusinessActor.query.filter_by(
            organization_id=organization_id,
            actor_type="Team",
        )
        .order_by(BusinessActor.name.asc())
        .all()
    )


def get_team(team_id: int, organization_id: int) -> Optional[BusinessActor]:
    """Return a team by id, scoped to the organisation."""
    return BusinessActor.query.filter_by(
        id=team_id,
        organization_id=organization_id,
        actor_type="Team",
    ).first()


def create_team(
    *,
    name: str,
    organization_id: int,
    description: str = "",
    headcount: int = 0,
) -> BusinessActor:
    """Create a new team (BusinessActor with actor_type='Team')."""
    team = BusinessActor(
        name=name,
        actor_type="Team",
        organization_id=organization_id,
        description=description or None,
        headcount=headcount,
    )
    db.session.add(team)
    db.session.commit()
    logger.info("Created team %s (id=%s) in org %s", name, team.id, organization_id)
    return team


def update_team(
    team_id: int,
    organization_id: int,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    headcount: Optional[int] = None,
) -> Optional[BusinessActor]:
    """Update a team's attributes."""
    team = get_team(team_id, organization_id)
    if team is None:
        return None
    if name is not None:
        team.name = name
    if description is not None:
        team.description = description
    if headcount is not None:
        team.headcount = headcount
    db.session.commit()
    return team


def delete_team(team_id: int, organization_id: int) -> bool:
    """Delete a team and its allocations."""
    team = get_team(team_id, organization_id)
    if team is None:
        return False
    db.session.delete(team)
    db.session.commit()
    return True


def get_allocations_for_team(team_id: int, organization_id: int):
    """Return all allocations for a team, scoped to the organisation."""
    return (
        TeamAllocation.query.filter_by(
            team_id=team_id,
            organization_id=organization_id,
        )
        .order_by(TeamAllocation.element_type.asc(), TeamAllocation.element_id.asc())
        .all()
    )


def get_allocations_for_element(
    element_type: str,
    element_id: int,
    organization_id: int,
):
    """Return all team allocations for an element, scoped to the organisation."""
    if element_type not in ALLOCATION_ELEMENT_TYPES:
        raise InvalidElementType(
            f"Invalid element type '{element_type}'. Must be one of {ALLOCATION_ELEMENT_TYPES}"
        )
    return (
        TeamAllocation.query.filter_by(
            element_type=element_type,
            element_id=element_id,
            organization_id=organization_id,
        )
        .all()
    )


def _check_total_share(team_id: int, organization_id: int, new_share: int, exclude_id: Optional[int] = None):
    """Check that adding *new_share* does not exceed 100 percent for the team."""
    query = TeamAllocation.query.filter_by(
        team_id=team_id,
        organization_id=organization_id,
    )
    if exclude_id is not None:
        query = query.filter(TeamAllocation.id != exclude_id)
    current_total = sum(a.share_percent for a in query.all())
    if current_total + new_share > 100:
        raise AllocationOverflow(
            f"Adding {new_share}% would bring the team's total to "
            f"{current_total + new_share}%, exceeding the 100% limit. "
            f"Currently allocated: {current_total}%."
        )


def record_allocation(
    *,
    team_id: int,
    element_type: str,
    element_id: int,
    share_percent: int,
    organization_id: int,
    source: str = "recorded",
    allocation_date: Optional[date] = None,
) -> TeamAllocation:
    """Record a team allocation for an element.

    Raises ``AllocationOverflow`` if the team's total would exceed 100%.
    Raises ``InvalidElementType`` if the element type is not valid.
    """
    if element_type not in ALLOCATION_ELEMENT_TYPES:
        raise InvalidElementType(
            f"Invalid element type '{element_type}'. Must be one of {ALLOCATION_ELEMENT_TYPES}"
        )

    _check_total_share(team_id, organization_id, share_percent)

    allocation = TeamAllocation(
        team_id=team_id,
        element_type=element_type,
        element_id=element_id,
        share_percent=share_percent,
        source=source,
        allocation_date=allocation_date or date.today(),
        organization_id=organization_id,
    )
    db.session.add(allocation)
    db.session.commit()
    return allocation


def update_allocation(
    allocation_id: int,
    organization_id: int,
    *,
    share_percent: Optional[int] = None,
) -> Optional[TeamAllocation]:
    """Update a team allocation's share percent."""
    allocation = TeamAllocation.query.filter_by(
        id=allocation_id,
        organization_id=organization_id,
    ).first()
    if allocation is None:
        return None

    if share_percent is not None:
        _check_total_share(
            allocation.team_id,
            organization_id,
            share_percent,
            exclude_id=allocation_id,
        )
        allocation.share_percent = share_percent

    db.session.commit()
    return allocation


def delete_allocation(allocation_id: int, organization_id: int) -> bool:
    """Delete a team allocation."""
    allocation = TeamAllocation.query.filter_by(
        id=allocation_id,
        organization_id=organization_id,
    ).first()
    if allocation is None:
        return False
    db.session.delete(allocation)
    db.session.commit()
    return True


def derive_allocations_for_team(team_id: int, organization_id: int):
    """Derive technology allocations from the applications a team owns.

    For each application the team owns, find the technologies it runs on
    and create derived allocations for those technologies.
    """
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    team = get_team(team_id, organization_id)
    if team is None:
        return []

    # Get applications this team owns (via the M2M relationship)
    owned_apps = team.applications or []

    if not owned_apps:
        return []

    # For each application, find the technologies it runs on
    # (relationships from application to technology elements)
    derived = []
    for app in owned_apps:
        if not app.archimate_element_id:
            continue
        # Find technology elements related to this application
        tech_rels = (
            ArchiMateRelationship.query.filter(
                ArchiMateRelationship.source_id == app.archimate_element_id,
            )
            .all()
        )
        for rel in tech_rels:
            target = db.session.get(ArchiMateElement, rel.target_id)
            if target and target.layer == "technology":
                # Check if allocation already exists
                existing = TeamAllocation.query.filter_by(
                    team_id=team_id,
                    element_type="technology",
                    element_id=target.id,
                    organization_id=organization_id,
                    source="derived",
                ).first()
                if existing:
                    continue
                # Calculate derived share: equal split across owned apps
                share = max(1, 100 // len(owned_apps)) if owned_apps else 0
                try:
                    alloc = record_allocation(
                        team_id=team_id,
                        element_type="technology",
                        element_id=target.id,
                        share_percent=share,
                        organization_id=organization_id,
                        source="derived",
                    )
                    derived.append(alloc)
                except AllocationOverflow:
                    continue

    return derived


def get_team_total_share(team_id: int, organization_id: int) -> int:
    """Return the total allocated share for a team."""
    total = (
        db.session.query(db.func.coalesce(db.func.sum(TeamAllocation.share_percent), 0))
        .filter_by(team_id=team_id, organization_id=organization_id)
        .scalar()
    )
    return int(total)


def get_teams_for_application(application_id: int, organization_id: int):
    """Return teams and their allocations for an application."""
    from app.models.application_portfolio import ApplicationComponent

    app_obj = ApplicationComponent.query.filter_by(
        id=application_id, organization_id=organization_id
    ).first()
    if app_obj is None:
        return []

    allocations = get_allocations_for_element("application", application_id, organization_id)
    result = []
    for alloc in allocations:
        team = get_team(alloc.team_id, organization_id)
        if team:
            result.append({"team": team, "allocation": alloc})
    return result


def get_teams_for_technology(technology_id: int, organization_id: int):
    """Return teams and their allocations for a technology element."""
    allocations = get_allocations_for_element("technology", technology_id, organization_id)
    result = []
    for alloc in allocations:
        team = get_team(alloc.team_id, organization_id)
        if team:
            result.append({"team": team, "allocation": alloc})
    return result