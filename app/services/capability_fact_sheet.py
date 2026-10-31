"""Capability Fact Sheet — the single-source-of-truth lens for one capability.

The Fact Sheet pattern proved itself on applications; a business architect lives
in *capabilities* and had no equivalent. This assembles everything the tool knows
about one business capability — identity and ownership, the maturity position
(where it is vs. where it needs to be), its strategic weight, the applications
that realise it, its sub-capabilities, and the ArchiMate element behind it (so
the blast-radius graph is one click away) — with the same completeness score that
makes the gaps in the record impossible to ignore.

Reads only; invents nothing. An unrecorded field is reported missing, never
filled with a plausible default.
"""
from __future__ import annotations

from typing import Any, Dict, List

from app import db

_COMPLETENESS_FIELDS = [
    ("description", "Description", 1),
    ("business_domain", "Business domain", 2),
    ("business_owner", "Business owner", 3),
    ("it_owner", "IT owner", 2),
    ("current_maturity_level", "Current maturity", 3),
    ("target_maturity_level", "Target maturity", 3),
    ("strategic_importance", "Strategic importance", 2),
    ("business_value", "Business value", 2),
]


def _has_value(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip() != ""
    return True


def _completeness(cap: Any) -> Dict[str, Any]:
    got = total = 0
    missing: List[str] = []
    for attr, label, weight in _COMPLETENESS_FIELDS:
        total += weight
        if _has_value(getattr(cap, attr, None)):
            got += weight
        else:
            missing.append(label)
    pct = round(100 * got / total) if total else 0
    band = "good" if pct >= 80 else "warn" if pct >= 50 else "poor"
    return {"pct": pct, "band": band, "missing": missing,
            "filled": len(_COMPLETENESS_FIELDS) - len(missing),
            "of": len(_COMPLETENESS_FIELDS)}


def _maturity(cap: Any) -> Dict[str, Any]:
    """Current vs target maturity, as levels and a % of a 5-level scale."""
    cur = getattr(cap, "current_maturity_level", None)
    tgt = getattr(cap, "target_maturity_level", None)
    gap = getattr(cap, "maturity_gap", None)
    if gap is None and cur is not None and tgt is not None:
        try:
            gap = tgt - cur
        except Exception:  # noqa: BLE001
            gap = None

    def _pct(v):
        try:
            return max(0, min(100, round(100 * float(v) / 5.0)))
        except Exception:  # noqa: BLE001
            return None

    tone = "good"
    if gap is not None:
        tone = "crit" if gap >= 2 else "warn" if gap >= 1 else "good"
    return {"current": cur, "target": tgt, "gap": gap,
            "current_pct": _pct(cur), "target_pct": _pct(tgt), "tone": tone,
            "assessed": getattr(cap, "maturity_assessment_date", None)}


def _realizing_apps(cap_id: int) -> List[Dict[str, Any]]:
    """Applications that realise / cover this capability."""
    from app.models.business_capabilities import (  # noqa: PLC0415
        ApplicationCapabilityCoverage,
    )
    from app.models.application_portfolio import ApplicationComponent  # noqa: PLC0415
    rows = (db.session.query(ApplicationCapabilityCoverage, ApplicationComponent)
            .join(ApplicationComponent,
                  ApplicationCapabilityCoverage.application_component_id == ApplicationComponent.id)
            .filter(ApplicationCapabilityCoverage.capability_id == cap_id)
            .limit(60).all())
    out = []
    seen = set()
    for cov, app in rows:
        seen.add(app.id)
        out.append({
            "id": app.id,
            "name": getattr(app, "name", None) or f"Application {app.id}",
            "support_level": getattr(cov, "support_level", None),
        })
    # The capability map and the mapping writers record applications in
    # application_capability_mapping; the record page shows both so a box's
    # application count and its record never disagree.
    from app.models.application_capability import (  # noqa: PLC0415
        ApplicationCapabilityMapping,
    )
    mapped = (db.session.query(ApplicationCapabilityMapping, ApplicationComponent)
              .join(ApplicationComponent,
                    ApplicationCapabilityMapping.application_component_id == ApplicationComponent.id)
              .filter(ApplicationCapabilityMapping.business_capability_id == cap_id)
              .limit(60).all())
    for mapping, app in mapped:
        if app.id in seen:
            continue
        seen.add(app.id)
        out.append({
            "id": app.id,
            "name": getattr(app, "name", None) or f"Application {app.id}",
            "support_level": getattr(mapping, "support_level", None),
        })
    return out


def _sub_capabilities(cap_id: int) -> List[Dict[str, Any]]:
    from app.models.business_capabilities import BusinessCapability  # noqa: PLC0415
    kids = (BusinessCapability.query
            .filter_by(parent_capability_id=cap_id)
            .order_by(BusinessCapability.name).limit(60).all())
    return [{"id": k.id, "name": getattr(k, "name", None) or f"Capability {k.id}",
             "maturity": getattr(k, "current_maturity_level", None)} for k in kids]


def unified_capability_for(cap: Any) -> Any:
    """The one UnifiedCapability row that holds the type judgment for ``cap``."""
    from app.models.unified_capability import UnifiedCapability  # noqa: PLC0415

    return (UnifiedCapability.query
            .filter_by(source_table="business_capability", source_id=str(cap.id),
                       organization_id=cap.organization_id)
            .first())


def build_capability_fact_sheet(cap: Any) -> Dict[str, Any]:
    """Assemble the full fact sheet for one BusinessCapability instance."""
    unified = unified_capability_for(cap)
    owners = []
    if unified is not None and cap.organization_id is not None:
        from app.models.application_owner import ApplicationOwner  # noqa: PLC0415

        owners = ApplicationOwner.get_display_rows_for_element(
            "capability", unified.id, cap.organization_id
        )
    return {
        "owners": owners,
        "capability_type": getattr(unified, "capability_type", None),
        "cap": cap,
        "completeness": _completeness(cap),
        "maturity": _maturity(cap),
        "apps": _realizing_apps(cap.id),
        "children": _sub_capabilities(cap.id),
        "archimate_element_id": getattr(cap, "archimate_element_id", None),
    }
