"""Application Fact Sheet — the single-source-of-truth lens for one application.

An architect's recurring problem is that everything known about an application is
true somewhere in the tool but assembled nowhere. This service gathers it into
one page: identity and ownership, cost, the technology it runs on, its security
and lifecycle posture (including whether it is heading for end-of-life), the
business capabilities it realises, what it depends on and what depends on it
(from the ArchiMate graph), and the diagrams it appears in — plus a
**completeness score** that makes the gaps in the record impossible to ignore.

Everything here reads existing data; the service invents nothing. A field that
is not recorded is reported as missing (which is the point of the score), never
filled with a plausible-looking default.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from app import db
from app.services.application_cost_accessor import get_annual_cost


# The fields that a well-governed application record should carry. Weighted so
# the score reflects decision-relevance, not just field count: ownership, cost,
# criticality and lifecycle matter more to a portfolio decision than a support
# URL. This is the rubric the completeness ring is scored against.
# Cost is checked via the accessor (get_annual_cost) rather than a direct
# attribute, so the single accessor principle is upheld.
_COMPLETENESS_FIELDS = [
    ("application_owner", "Owner", 3),
    ("business_domain", "Business domain", 2),
    ("business_criticality", "Business criticality", 3),
    ("lifecycle_status", "Lifecycle", 3),
    ("total_cost_of_ownership", "Total cost of ownership", 3),
    ("technology_stack", "Technology stack", 2),
    ("deployment_model", "Hosting / deployment", 2),
    ("data_classification", "Data classification", 2),
    ("vendor_name", "Vendor", 2),
    ("disaster_recovery_enabled", "Disaster recovery", 1),
    ("description", "Description", 1),
]


def _has_cost_value(app: Any) -> bool:
    """Check if the application has a recorded annual cost via the accessor."""
    return get_annual_cost(app) is not None

# Lifecycle stages we treat as "sunset" for the end-of-life signal.
_SUNSET_STAGES = {"retiring", "sunset", "decommissioning", "end_of_life",
                  "end-of-life", "deprecated", "retire"}


def _has_value(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip() != ""
    return True


def _owner_count(app_id: int, org_id: int) -> int:
    """Read ApplicationOwner rows for *app_id* in the given organisation."""
    from app.models.application_owner import ApplicationOwner
    return ApplicationOwner.query.filter(
        ApplicationOwner.application_id == app_id,
        ApplicationOwner.organization_id == org_id,
    ).count()


def compute_completeness(app: Any, owner_count: Optional[int] = None) -> Dict[str, Any]:
    """Weighted % of key fields populated, plus the list of what is missing.

    The "Owner" field is considered populated when there is at least one
    ApplicationOwner row for this application, regardless of the legacy
    text column.
    """
    org_id = getattr(app, "organization_id", None)
    got = 0
    total = 0
    missing: List[str] = []
    owner_count = owner_count if owner_count is not None else (_owner_count(app.id, org_id) if org_id else 0)
    for attr, label, weight in _COMPLETENESS_FIELDS:
        total += weight
        if attr == "application_owner":
            if owner_count > 0:
                got += weight
            else:
                missing.append(label)
        elif attr == "total_cost_of_ownership":
            if _has_cost_value(app):
                got += weight
            else:
                missing.append(label)
        elif _has_value(getattr(app, attr, None)):
            got += weight
        else:
            missing.append(label)
    pct = round(100 * got / total) if total else 0
    band = "good" if pct >= 80 else "warn" if pct >= 50 else "poor"
    return {"pct": pct, "band": band, "missing": missing,
            "filled": len(_COMPLETENESS_FIELDS) - len(missing),
            "of": len(_COMPLETENESS_FIELDS)}


def _lifecycle_signal(app: Any) -> Dict[str, Any]:
    """End-of-life / obsolescence read: stage, retirement date, days remaining."""
    stage = (getattr(app, "lifecycle_status", None)
             or getattr(app, "current_lifecycle_state", None) or "").strip()
    retire: Optional[date] = getattr(app, "planned_retirement_date", None)
    days_left = None
    # date.today() is unavailable-free here (real request context); guard anyway.
    if retire is not None:
        try:
            days_left = (retire - date.today()).days
        except Exception:  # noqa: BLE001
            days_left = None
    sunset = stage.lower() in _SUNSET_STAGES or (
        days_left is not None and days_left <= 365)
    tone = "crit" if (days_left is not None and days_left <= 90) else (
        "warn" if sunset else "good")
    return {"stage": stage or None, "retirement_date": retire,
            "days_left": days_left, "is_sunset": sunset, "tone": tone}


def _capabilities(app_id: int, org_id: Optional[int]) -> List[Dict[str, Any]]:
    """Business capabilities this application realises, with support level.

    Imports are deliberately unguarded: a missing model module is a defect, and
    swallowing it would render "no capabilities mapped" — indistinguishable from
    a truthful empty result on a page branded a single source of truth.
    """
    from app.models.application_capability import (  # noqa: PLC0415
        ApplicationCapabilityMapping,
    )
    from app.models.business_capabilities import BusinessCapability  # noqa: PLC0415

    # ApplicationCapabilityMapping carries organization_id but does NOT inherit
    # TenantMixin, so do_orm_execute injects no tenant predicate — it must be
    # scoped by hand or the join reads every organisation's mappings.

    if org_id is None:
        raise ValueError(
            "application has no organization_id; refusing to run an unscoped "
            "ApplicationCapabilityMapping query"
        )
    q = (db.session.query(ApplicationCapabilityMapping, BusinessCapability)
         .join(BusinessCapability,
               BusinessCapability.id == ApplicationCapabilityMapping.business_capability_id)
         .filter(ApplicationCapabilityMapping.application_component_id == app_id)
         .filter(ApplicationCapabilityMapping.organization_id == org_id))
    out = []
    for mapping, cap in q.all():
        out.append({
            "id": cap.id,
            "name": getattr(cap, "name", None) or f"Capability #{cap.id}",
            "support_level": getattr(mapping, "support_level", None),
        })
    return out


def _dependencies(app: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Upstream/downstream from the ArchiMate graph via the linked element."""
    el_id = getattr(app, "archimate_element_id", None)
    if not el_id:
        return {"upstream": [], "downstream": [], "linked": False}
    try:
        from app.models.archimate_core import (  # noqa: PLC0415
            ArchiMateElement, ArchiMateRelationship,
        )
    except Exception:  # noqa: BLE001
        return {"upstream": [], "downstream": [], "linked": True}

    def _name(eid):
        el = db.session.get(ArchiMateElement, eid)
        return (getattr(el, "name", None), getattr(el, "type", None)) if el else (None, None)

    from app.services.impact_graph import dependency_ends  # noqa: PLC0415

    downstream, upstream = [], []
    rels = ArchiMateRelationship.query.filter(
        db.or_(ArchiMateRelationship.source_id == el_id, ArchiMateRelationship.target_id == el_id)
    ).all()
    for r in rels:
        other = r.target_id if r.source_id == el_id else r.source_id
        nm, tp = _name(other)
        if not nm:
            continue
        # The arrow's direction is not the dependency's: "this serves X" means
        # X depends on this. dependency_ends is the one reading of that; a
        # relationship carrying no dependency (association) keeps its arrow.
        ends = dependency_ends(r.type, r.source_id, r.target_id)
        depends_on_other = ends[0] == el_id if ends else r.source_id == el_id
        (downstream if depends_on_other else upstream).append({"name": nm, "type": tp, "rel": r.type})
    return {"upstream": upstream, "downstream": downstream, "linked": True}


def _diagrams(app: Any) -> List[Dict[str, Any]]:
    """Saved diagrams this application's element appears on."""
    el_id = getattr(app, "archimate_element_id", None)
    if not el_id:
        return []
    from app.models.archimate_core import (  # noqa: PLC0415
        SavedDiagram, SavedDiagramElement,
    )
    # Unguarded for the same reason as _capabilities: an import failure here is a
    # defect, and returning [] would read on the page as "appears in no diagrams".
    q = (db.session.query(SavedDiagram)
         .join(SavedDiagramElement, SavedDiagramElement.diagram_id == SavedDiagram.id)
         .filter(SavedDiagramElement.element_id == el_id).distinct())
    return [{"id": d.id, "name": getattr(d, "name", None) or f"Diagram #{d.id}"}
            for d in q.all()]


def _linked_risks(app_id: int) -> List[Dict[str, Any]]:
    """Risks mapped to this application via the Risk Register's
    "Map to…" picker (RiskEntityLink). Import deferred to avoid a module-load
    cycle (risk_service imports app.services.archimate_backbone)."""
    from app.services.risk_service import links_for_entity
    return links_for_entity("application", app_id)


def build_fact_sheet(app: Any) -> Dict[str, Any]:
    """Assemble the full fact sheet for one ApplicationComponent instance."""
    org_id = getattr(app, "organization_id", None)
    annual_cost = get_annual_cost(app)
    license_cost = getattr(app, "license_cost_annual", None)
    from app.models.application_owner import ApplicationOwner  # noqa: PLC0415

    application_owners = ApplicationOwner.get_display_rows_for_application(app.id, org_id) if org_id else []

    return {
        "app": app,
        "completeness": compute_completeness(app, owner_count=len(application_owners)),
        "lifecycle": _lifecycle_signal(app),
        "capabilities": _capabilities(app.id, org_id),
        "dependencies": _dependencies(app),
        "diagrams": _diagrams(app),
        "linked_risks": _linked_risks(app.id),
        "annual_cost_formatted": ('{:,.0f}'.format(annual_cost) if annual_cost is not None else '—'),
        "license_cost_formatted": ('{:,.0f}'.format(license_cost) if license_cost is not None else '—'),
        "application_owners": application_owners,
    }
