"""Ownership coverage view for CTO and portfolio manager personas.

Shows each business unit and which of its applications have a named owner,
using the single ownership definition in my_applications/services.py.
"""

from __future__ import annotations

import logging

from flask import g, render_template
from app import db
from flask_login import login_required
from sqlalchemy import func

from app.decorators import audit_log, role_required
from app.modules.my_applications.services import has_assigned_owner
from app.models.application_portfolio import ApplicationComponent
from app.models.user import ROLE_CTO, ROLE_PORTFOLIO_MANAGER

from . import unified_applications_bp

logger = logging.getLogger(__name__)


def _domain_matches_unit(unit_name: str):
    """Case- and whitespace-normalised business-domain match for one unit."""
    normalized_unit = (unit_name or "").strip().lower()
    return func.lower(func.trim(ApplicationComponent.business_domain)) == normalized_unit


def _coverage_footer(unit_name, applications, owned, recorded, domain):
    """The footer for one coverage figure: the applications counted, the owners found among them, the unit."""
    from app.services import result_footer_service as footers

    portfolio = footers.page_link("unified_applications.application_list", domain=domain) if domain else \
        footers.page_link("unified_applications.application_list")
    missing = applications == 0
    return footers.summary_footer([
        footers.fact("Business unit: %s" % unit_name, source_kind="manual", subject="Unit", as_of=recorded),
        footers.fact("Applications counted: %s" % (applications or "none in this unit"), href=portfolio, source_kind="derived",
                     subject="Applications", as_of=recorded, missing=missing),
        footers.fact("With a named owner: %s" % (owned if applications else "no applications to count"), href=footers.page_link("unified_applications.application_list",
                                                                               no_owner="1"),
                     source_kind="manual", subject="Owners", as_of=recorded, missing=missing),
    ])


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

    # When each unit's applications were last recorded: one grouped query, not one per unit.
    latest = {
        domain: stamp
        for domain, stamp in db.session.query(
            func.lower(func.trim(ApplicationComponent.business_domain)), func.max(ApplicationComponent.updated_at)
        ).filter(ApplicationComponent.organization_id == org_id)
        .group_by(func.lower(func.trim(ApplicationComponent.business_domain))).all()
    }

    rows = []
    total_apps = 0
    total_owned = 0
    for unit in units:
        unit_apps = (
            ApplicationComponent.query
            .filter(ApplicationComponent.organization_id == org_id)
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
            "footer": _coverage_footer(unit.name, unit_apps, unit_owned,
                                       latest.get((unit.name or "").strip().lower()), unit.name),
        })

    # Also include applications that have no business_domain set
    orphan_count = ApplicationComponent.query.filter(
        ApplicationComponent.organization_id == org_id,
        ApplicationComponent.business_domain.is_(None),
    ).count()
    orphan_owned = 0
    if orphan_count > 0:
        orphan_owned = ApplicationComponent.query.filter(
            ApplicationComponent.organization_id == org_id,
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
                "footer": _coverage_footer("No business unit", orphan_count, orphan_owned, latest.get(None), None),
            })

    return render_template(
        "applications/ownership_coverage.html",
        rows=rows,
        total_apps=total_apps,
        total_owned=total_owned,
        overall_pct=round(100 * total_owned / total_apps) if total_apps > 0 else None,
        organisation_unit_count=len(units),
        footer=_coverage_footer("All business units", total_apps, total_owned,
                                max((v for v in latest.values() if v is not None), default=None), None),
        forbidden=False,
    )
