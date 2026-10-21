"""The four headline figures every portfolio screen shows, each read from its one store.

    owners   applications that have an owner       -> has_assigned_owner (business owner or ApplicationOwner)
    cost     annual cost of the applications       -> ApplicationComponent.total_cost_of_ownership,
                                                      through application_cost_accessor
    vendors  vendor organisations in the catalogue -> VendorOrganization (one shared catalogue)
    gaps     recorded gaps                         -> gap register, through gap_register_service.count_gaps

A screen that shows one of these numbers calls the function here, so two screens cannot
answer the same question from two stores (ADR 0008). Every figure is scoped to the
organisation passed in; None means "could not be measured" and renders as a dash, never 0.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app import db


def owner_figure(org_id: Optional[int]) -> Dict[str, Optional[int]]:
    """Applications of `org_id` that have an owner, out of all its applications.

    "Has an owner" is the one predicate the application list already uses
    (my_applications.services.has_assigned_owner): a business owner recorded on the
    application, or an owner assignment held by a user of the same organisation.
    """
    if org_id is None:
        return {"applications_with_owner": None, "application_count": None}
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.my_applications.services import has_assigned_owner

    # Same population as the application list's total: duplicates are not applications.
    base = db.session.query(db.func.count(ApplicationComponent.id)).filter(
        ApplicationComponent.organization_id == org_id,
        ~ApplicationComponent.name.ilike("(Duplicate)%", escape="\\"),
    )
    return {
        "applications_with_owner": base.filter(has_assigned_owner(org_id)).scalar() or 0,
        "application_count": base.scalar() or 0,
    }


def cost_figure(org_id: Optional[int]) -> Dict[str, Any]:
    """Total annual application cost for `org_id` (None when nothing is recorded)."""
    if org_id is None:
        return {"total_annual_cost": None, "application_count": None, "applications_with_cost": None}
    from app.services.application_cost_accessor import get_cost_summary_for_org

    return get_cost_summary_for_org(org_id)


def cost_by_application(org_id: Optional[int], limit: int = 10) -> list:
    """The applications behind `cost_figure`, largest first, as (name, annual cost) pairs.

    Same population and column as cost_figure (every application of the organisation with a
    recorded annual cost), so the rows add up to its total. When more than `limit` applications
    carry a cost the rest are folded into one final "Other applications" pair.
    """
    if org_id is None:
        return []
    from app.models.application_portfolio import ApplicationComponent

    rows = (
        db.session.query(ApplicationComponent.name, ApplicationComponent.total_cost_of_ownership)
        .filter(
            ApplicationComponent.organization_id == org_id,
            ApplicationComponent.total_cost_of_ownership.isnot(None),
        )
        .order_by(ApplicationComponent.total_cost_of_ownership.desc(), ApplicationComponent.name)
        .all()
    )
    pairs = [(name or "Unnamed application", float(cost)) for name, cost in rows]
    if len(pairs) > limit:
        pairs = pairs[:limit] + [("Other applications", sum(c for _, c in pairs[limit:]))]
    return pairs


def vendor_count() -> int:
    """Vendor organisations in the one shared vendor catalogue."""
    from app.models.vendor.vendor_organization import VendorOrganization

    return db.session.query(db.func.count(VendorOrganization.id)).scalar() or 0


def gap_count(org_id: Optional[int]) -> Optional[int]:
    """Gaps in the one gap register for `org_id` (the count the roadmap and Gap Analysis show)."""
    if org_id is None:
        return None
    from app.services.gap_register_service import count_gaps

    return count_gaps(org_id)


def percent(part: Optional[int], whole: Optional[int]) -> Optional[int]:
    """Whole-number percentage; None when there is nothing to measure against."""
    if part is None or not whole:
        return None
    return round(part / whole * 100)
