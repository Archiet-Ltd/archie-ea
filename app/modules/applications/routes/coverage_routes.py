"""Ownership coverage view for CTO and portfolio manager personas.

Shows each business unit and which of its applications have a named owner,
using the single ownership definition in my_applications/services.py.
"""

from __future__ import annotations

import logging

from flask import g, render_template
from flask_login import login_required
from sqlalchemy import func

from app.decorators import audit_log, role_required
from app.modules.my_applications.services import has_assigned_owner
from app.models.application_portfolio import ApplicationComponent
from app.modules.dashboard.services.overview_figures import _active_applications
from app.models.user import ROLE_CTO, ROLE_PORTFOLIO_MANAGER

from . import unified_applications_bp

logger = logging.getLogger(__name__)


def _domain_matches_unit(unit_name: str):
    """Case- and whitespace-normalised business-domain match for one unit."""
    normalized_unit = (unit_name or "").strip().lower()
    return func.lower(func.trim(ApplicationComponent.business_domain)) == normalized_unit


@unified_applications_bp.route("/ownership-coverage")
@login_required
@role_required(ROLE_CTO, ROLE_PORTFOLIO_MANAGER)
@audit_log("ownership_coverage_view")
def ownership_coverage():
    """Ownership coverage by business unit.

    Accessible by cto and portfolio_manager personas from their sidebar.
    Shows every business unit with applications in the caller's organisation.
    A unit with no applications shows ``—`` as its coverage figures.
    """

    org_id = g.current_org_id

    from app.models.enterprise_intelligence import OrganizationUnit

    units = (
        OrganizationUnit.query
        .filter(OrganizationUnit.organization_id == org_id)
        .order_by(OrganizationUnit.name)
        .all()
    )

    owner_predicate = has_assigned_owner(org_id)

    # The Overview's own definition of an application, so both pages count the same ones.
    active_ids = _active_applications().where(ApplicationComponent.organization_id == org_id).with_only_columns(
        ApplicationComponent.id
    )
    all_count = ApplicationComponent.query.filter(ApplicationComponent.organization_id == org_id).count()

    rows = []
    total_apps = 0
    total_owned = 0
    for unit in units:
        unit_apps = (
            ApplicationComponent.query
            .filter(ApplicationComponent.organization_id == org_id)
            .filter(ApplicationComponent.id.in_(active_ids))
            .filter(_domain_matches_unit(unit.name))
            .count()
        )
        unit_owned = 0
        if unit_apps > 0:
            unit_owned = (
                ApplicationComponent.query
                .filter(ApplicationComponent.organization_id == org_id)
                .filter(_domain_matches_unit(unit.name))
                .filter(owner_predicate)
                .count()
            )
        total_apps += unit_apps
        total_owned += unit_owned
        rows.append({
            "unit_name": unit.name,
            "total_apps": unit_apps,
            "owned": unit_owned,
            "coverage_pct": round(100 * unit_owned / unit_apps) if unit_apps > 0 else None,
        })

    # Also include applications that have no business_domain set
    orphan_count = ApplicationComponent.query.filter(
        ApplicationComponent.organization_id == org_id,
        ApplicationComponent.id.in_(active_ids),
        ApplicationComponent.business_domain.is_(None),
    ).count()
    orphan_owned = 0
    if orphan_count > 0:
        orphan_owned = ApplicationComponent.query.filter(
            ApplicationComponent.organization_id == org_id,
            ApplicationComponent.id.in_(active_ids),
            ApplicationComponent.business_domain.is_(None),
            owner_predicate,
        ).count()
        total_apps += orphan_count
        total_owned += orphan_owned
        if orphan_count > 0:
            rows.append({
                "unit_name": "No business unit",
                "total_apps": orphan_count,
                "owned": orphan_owned,
                "coverage_pct": round(100 * orphan_owned / orphan_count),
            })

    return render_template(
        "applications/ownership_coverage.html",
        rows=rows,
        total_apps=total_apps,
        total_owned=total_owned,
        overall_pct=round(100 * total_owned / total_apps) if total_apps > 0 else None,
        organisation_unit_count=len(units),
        left_out_count=_left_out(org_id, all_count, active_ids),
        forbidden=False,
    )


def _left_out(org_id, all_count, active_ids):
    """How many of the organisation's applications the figures leave out, as the Overview does."""
    counted = ApplicationComponent.query.filter(
        ApplicationComponent.organization_id == org_id, ApplicationComponent.id.in_(active_ids)
    ).count()
    return all_count - counted
