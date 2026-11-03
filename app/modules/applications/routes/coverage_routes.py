"""Ownership coverage view for CTO and portfolio manager personas.

Shows each business unit and which of its applications have a named owner,
using the single ownership definition in my_applications/services.py.
"""

from __future__ import annotations

import logging
import re

from flask import g, render_template
from flask_login import login_required
from sqlalchemy import func
from app import db

from app.decorators import audit_log, role_required
from app.modules.my_applications.services import has_assigned_owner
from app.models.application_portfolio import ApplicationComponent
from app.modules.dashboard.services.overview_figures import _active_applications, left_out_applications
from app.models.user import ROLE_CTO, ROLE_PORTFOLIO_MANAGER
from app.services.element_ownership_service import OWNER_TEXT_BLANKS

from . import unified_applications_bp

logger = logging.getLogger(__name__)


_SPACES = "[" + OWNER_TEXT_BLANKS + "]+"


def _normalised(text) -> str:
    """A unit or domain name with case and runs of spaces ignored."""
    return re.sub(_SPACES, " ", text or "").strip(" ").lower()


def _domain_matches_unit(unit_name: str):
    """Case- and whitespace-normalised business-domain match for one unit, as ``_normalised`` reads it."""
    domain = func.lower(func.trim(func.regexp_replace(ApplicationComponent.business_domain, _SPACES, " ", "g")))
    return domain == _normalised(unit_name)


def _distinct_units(units):
    """Keep one unit for each normalised name: the first by name, then by id.

    Returns the kept units and the (kept name, merged name) pairs, so the page can say
    which unit took the applications of the others.
    """
    kept, merged = {}, []
    for unit in sorted(units, key=lambda u: (u.name or "", u.id)):
        key = _normalised(unit.name)
        if key in kept:
            merged.append((kept[key].name, unit.name))
        else:
            kept[key] = unit
    return kept, merged


def _coverage_footer(unit_name, applications, owned, recorded, domain, no_unit=False):
    """The footer for one coverage figure: the applications counted, the owners found among them, the unit.
    Each link opens the applications that figure counted: this unit's (or those with no unit), the active
    ones the list shows by default."""
    from app.services import result_footer_service as footers

    scope = {}
    if no_unit:
        scope["no_domain"] = "1"
    elif domain:
        scope["domain"] = domain
    portfolio = footers.page_link("unified_applications.application_list", **scope)
    missing = applications == 0
    return footers.summary_footer([
        footers.fact("Business unit: %s" % unit_name, source_kind="manual", subject="Unit", as_of=recorded),
        footers.fact("Applications counted: %s" % (applications or "none in this unit"), href=portfolio, source_kind="derived",
                     subject="Applications", as_of=recorded, missing=missing),
        footers.fact("With a named owner: %s" % (owned if applications else "no applications to count"),
                     href=footers.page_link("unified_applications.application_list", has_owner="1", **scope),
                     source_kind="manual", subject="Owners", as_of=recorded, missing=missing),
        footers.fact("Without an owner: %s" % (applications - owned if applications else "no applications to count"),
                     href=footers.page_link("unified_applications.application_list", no_owner="1", **scope),
                     source_kind="derived", subject="Owners", as_of=recorded, missing=missing),
    ])


def outside_every_unit(org_id):
    """Matches an application with no business domain, or one whose domain is no unit's: the applications
    the "No business unit" row counts."""
    from app.models.enterprise_intelligence import OrganizationUnit

    units = OrganizationUnit.query.filter(OrganizationUnit.organization_id == org_id).all()
    in_a_unit = [_domain_matches_unit(unit.name) for unit in _distinct_units(units)[0].values()]
    if not in_a_unit:
        return db.true()
    return db.not_(db.or_(*in_a_unit)) | ApplicationComponent.business_domain.is_(None)


def coverage_figures(org_id) -> dict:
    """Every figure the ownership coverage page shows, for one organisation."""
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

    def in_org_active():
        return ApplicationComponent.query.filter(
            ApplicationComponent.organization_id == org_id, ApplicationComponent.id.in_(active_ids)
        )

    # When each unit's applications were last recorded: one grouped query, not one per unit.
    latest = {
        domain: stamp
        for domain, stamp in db.session.query(
            func.lower(func.trim(ApplicationComponent.business_domain)), func.max(ApplicationComponent.updated_at)
        ).filter(ApplicationComponent.organization_id == org_id)
        .group_by(func.lower(func.trim(ApplicationComponent.business_domain))).all()
    }

    kept, merged_units = _distinct_units(units)
    rows = []
    for unit in units:
        if kept[_normalised(unit.name)] is not unit:
            rows.append({"unit_name": unit.name, "total_apps": 0, "owned": 0, "coverage_pct": None,
                         "footer": _coverage_footer(unit.name, 0, 0, None, (unit.name or "").strip())})
            continue
        in_unit = in_org_active().filter(_domain_matches_unit(unit.name))
        unit_apps = in_unit.count()
        unit_owned = in_unit.filter(owner_predicate).count() if unit_apps > 0 else 0
        rows.append({
            "unit_name": unit.name,
            "total_apps": unit_apps,
            "owned": unit_owned,
            "coverage_pct": round(100 * unit_owned / unit_apps) if unit_apps > 0 else None,
            "footer": _coverage_footer(unit.name, unit_apps, unit_owned,
                                       latest.get((unit.name or "").strip().lower()), (unit.name or "").strip()),
        })

    # Every other active application: no domain, or a domain that matches no unit.
    outside = in_org_active().filter(outside_every_unit(org_id))
    outside_count = outside.count()
    unmatched_domains = []
    if outside_count:
        outside_owned = outside.filter(owner_predicate).count()
        unmatched_domains = sorted({
            d.strip() for (d,) in outside.with_entities(ApplicationComponent.business_domain).distinct()
            if d and d.strip()
        })
        rows.append({
            "unit_name": "No business unit",
            "total_apps": outside_count,
            "owned": outside_owned,
            "coverage_pct": round(100 * outside_owned / outside_count),
            "footer": _coverage_footer("No business unit", outside_count, outside_owned, latest.get(None), None, no_unit=True),
        })

    total_apps = sum(r["total_apps"] for r in rows)
    total_owned = sum(r["owned"] for r in rows)

    return {
        "rows": rows,
        "total_apps": total_apps,
        "total_owned": total_owned,
        "overall_pct": round(100 * total_owned / total_apps) if total_apps > 0 else None,
        "organisation_unit_count": len(units),
        "left_out": left_out_applications(org_id),
        "outside_count": outside_count,
        "unmatched_domains": unmatched_domains,
        "merged_units": merged_units,
        "footer": _coverage_footer("All business units", total_apps, total_owned,
                                   max((v for v in latest.values() if v is not None), default=None), None),
    }


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

    return render_template(
        "applications/ownership_coverage.html",
        forbidden=False,
        **coverage_figures(g.current_org_id),
    )
