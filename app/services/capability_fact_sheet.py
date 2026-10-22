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


def application_mappings_for_capability(cap_id: int) -> Dict[int, Any]:
    """Every application mapped to one business capability, whichever store says so.

    The Capability Map row reads ``ApplicationCapabilityMapping``, the Investment
    Map reads ``UnifiedApplicationCapabilityMapping`` (through the capability's
    projection) and the mapping dialog historically read only
    ``ApplicationCapabilityCoverage``. Each screen gave a different answer for
    the same capability, so every screen that lists a capability's applications
    asks this one function. Returns ``{application_component_id: mapping row}``;
    a coverage row wins over the other two because it carries the dialog's
    editable detail, and the row store wins over the unified store.
    """
    from app.models.application_capability import ApplicationCapabilityMapping  # noqa: PLC0415
    from app.models.business_capabilities import (  # noqa: PLC0415
        ApplicationCapabilityCoverage,
    )
    from app.models.unified_application_capability_mapping import (  # noqa: PLC0415
        UnifiedApplicationCapabilityMapping,
    )
    from app.models.unified_capability import UnifiedCapability  # noqa: PLC0415

    found: Dict[int, Any] = {}

    unified_ids = [
        u.id
        for u in UnifiedCapability.query.filter_by(
            source_table="business_capability", source_id=str(cap_id)
        ).all()
    ]
    if unified_ids:
        # tenant-scoping-ok: the unified mapping table has no organisation
        # column; scoped through the unified capability ids of this
        # (tenant-scoped) capability.
        for m in UnifiedApplicationCapabilityMapping.query.filter(
            UnifiedApplicationCapabilityMapping.unified_capability_id.in_(unified_ids)
        ).all():
            found[m.application_component_id] = m
    # tenant-scoping-ok: filtered by one capability that the caller looked up
    # through the tenant-scoped BusinessCapability query.
    for m in ApplicationCapabilityMapping.query.filter_by(business_capability_id=cap_id).all():
        found[m.application_component_id] = m
    for m in ApplicationCapabilityCoverage.query.filter_by(capability_id=cap_id).all():
        found[m.application_component_id] = m
    return found


def _realizing_apps(cap_id: int) -> List[Dict[str, Any]]:
    """Applications that realise / cover this capability."""
    from app.models.application_portfolio import ApplicationComponent  # noqa: PLC0415
    mapped = application_mappings_for_capability(cap_id)
    if not mapped:
        return []
    apps = (ApplicationComponent.query
            .filter(ApplicationComponent.id.in_(list(mapped)))
            .order_by(ApplicationComponent.name).limit(60).all())
    return [{
        "id": app.id,
        "name": getattr(app, "name", None) or f"Application {app.id}",
        "support_level": getattr(mapped[app.id], "support_level", None),
    } for app in apps]


def _sub_capabilities(cap_id: int) -> List[Dict[str, Any]]:
    from app.models.business_capabilities import BusinessCapability  # noqa: PLC0415
    kids = (BusinessCapability.query
            .filter_by(parent_capability_id=cap_id)
            .order_by(BusinessCapability.name).limit(60).all())
    return [{"id": k.id, "name": getattr(k, "name", None) or f"Capability {k.id}",
             "maturity": getattr(k, "current_maturity_level", None)} for k in kids]


def build_capability_fact_sheet(cap: Any) -> Dict[str, Any]:
    """Assemble the full fact sheet for one BusinessCapability instance."""
    return {
        "cap": cap,
        "completeness": _completeness(cap),
        "maturity": _maturity(cap),
        "apps": _realizing_apps(cap.id),
        "children": _sub_capabilities(cap.id),
        "archimate_element_id": getattr(cap, "archimate_element_id", None),
    }
