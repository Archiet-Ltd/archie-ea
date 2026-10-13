"""What already covers a demand, and what to do about it.

A demand names the capability it is about. This reads the applications mapped
to that capability (the capability-to-application mapping the coverage lists
use -- no second coverage computation) and turns them into a recommendation.
The recommendation is advice: the person triaging records the route, and the
reasons shown at that moment are stored with it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from flask import g

from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
from app.models.unified_capability import UnifiedCapability

# A mapping counts as covering the capability when it is recorded as full
# support, or when its recorded coverage reaches this share.
FULL_COVERAGE_PERCENTAGE = 80


def _org_id() -> Optional[int]:
    """The signed-in organisation, or None when it is not resolved.

    The automatic tenant filters do nothing when this is None, so every read
    below states its organisation explicitly and returns nothing without one.
    """
    return getattr(g, "current_org_id", None)


def visible_capabilities(limit: int = 500) -> List[UnifiedCapability]:
    """Capabilities the signed-in organisation can pick from (its own and shared)."""
    org_id = _org_id()
    if org_id is None:
        return []
    return list(
        db.session.execute(
            db.select(UnifiedCapability)
            .where(UnifiedCapability.visibility_predicate(org_id))
            .order_by(UnifiedCapability.name)
            .limit(limit)
        ).scalars()
    )


def visible_capability(capability_id: int) -> Optional[UnifiedCapability]:
    """One capability by id if the signed-in organisation can see it, else None.

    A direct lookup, not membership in ``visible_capabilities`` (which is
    capped for the picker).
    """
    org_id = _org_id()
    if org_id is None:
        return None
    return UnifiedCapability.visible_to_organization(capability_id, org_id)


def coverage_by_capability(capability_ids) -> Dict[int, List[Dict[str, Any]]]:
    """Applications mapped to each capability, best coverage first, in one query.

    Only the signed-in organisation's live applications are read.
    ``coverage_percentage`` is None only when no figure was recorded; a recorded
    0 stays 0. The mapping column defaults to 0, so a row saved without a
    figure reads as a recorded 0.
    """
    out: Dict[int, List[Dict[str, Any]]] = {cid: [] for cid in capability_ids}
    org_id = _org_id()
    if org_id is None or not out:
        return out
    rows = db.session.execute(
        db.select(ApplicationComponent, UnifiedApplicationCapabilityMapping)
        .join(
            UnifiedApplicationCapabilityMapping,
            UnifiedApplicationCapabilityMapping.application_component_id
            == ApplicationComponent.id,
        )
        .where(
            UnifiedApplicationCapabilityMapping.unified_capability_id.in_(list(out)),
            UnifiedApplicationCapabilityMapping.is_active.is_(True),
            ApplicationComponent.organization_id == org_id,
            ApplicationComponent.deleted_at.is_(None),
        )
    ).all()
    for app, mapping in rows:
        out[mapping.unified_capability_id].append(
            {
                "application_id": app.id,
                "name": app.name,
                "support_level": mapping.support_level,
                "coverage_percentage": mapping.coverage_percentage,
            }
        )
    for entries in out.values():
        entries.sort(key=lambda r: (-(r["coverage_percentage"] or 0), r["name"]))
    return out


def coverage_for_capability(capability_id: int) -> List[Dict[str, Any]]:
    """Applications mapped to one capability, best coverage first."""
    return coverage_by_capability([capability_id])[capability_id]


def _covers_fully(entry: Dict[str, Any]) -> bool:
    return entry["support_level"] == "full" or (entry["coverage_percentage"] or 0) >= FULL_COVERAGE_PERCENTAGE


def recommend(capability_id: Optional[int]) -> Dict[str, Any]:
    """A recommendation with the applications and the sentence behind it.

    ``route`` is ``reuse``, ``extend`` (something partly covers it) or
    ``buy_or_build`` (nothing is mapped); None when the demand names no
    capability, because there is nothing to read coverage from.
    """
    return recommend_many([capability_id])[capability_id]


def recommend_many(capability_ids) -> Dict[Optional[int], Dict[str, Any]]:
    """``recommend`` for several capabilities with a fixed number of queries."""
    ids = {cid for cid in capability_ids if cid is not None}
    org_id = _org_id()
    capabilities: Dict[int, UnifiedCapability] = {}
    if ids and org_id is not None:
        capabilities = {
            c.id: c
            for c in db.session.execute(
                db.select(UnifiedCapability).where(
                    UnifiedCapability.id.in_(ids),
                    UnifiedCapability.visibility_predicate(org_id),
                )
            ).scalars()
        }
    coverage = coverage_by_capability(list(capabilities))
    return {cid: _recommendation(cid, capabilities.get(cid), coverage.get(cid, [])) for cid in set(capability_ids)}


def _recommendation(capability_id, capability, apps) -> Dict[str, Any]:
    rec = _recommendation_body(capability_id, capability, apps)
    rec["capability_name"] = capability.name if capability is not None else None
    return rec


def _recommendation_body(capability_id, capability, apps) -> Dict[str, Any]:
    if capability_id is None:
        return {
            "route": None,
            "applications": [],
            "reason": "No capability is recorded on this demand, so existing coverage cannot be read.",
        }
    if capability is None:
        return {"route": None, "applications": [], "reason": "The capability on this demand was not found."}
    names = ", ".join(a["name"] for a in apps[:5])
    full = [a for a in apps if _covers_fully(a)]
    if full:
        return {
            "route": "reuse",
            "applications": apps,
            "reason": "%s already supports %s: %s." % (
                "An application" if len(full) == 1 else "%d applications" % len(full),
                capability.name,
                ", ".join(a["name"] for a in full[:5]),
            ),
        }
    if apps:
        return {
            "route": "extend",
            "applications": apps,
            "reason": "%s is only partly supported (%s); extending an existing application may be enough." % (
                capability.name, names),
        }
    return {
        "route": "buy_or_build",
        "applications": [],
        "reason": "No application is mapped to %s." % capability.name,
    }
