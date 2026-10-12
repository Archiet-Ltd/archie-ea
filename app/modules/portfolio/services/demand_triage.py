"""What already covers a demand, and what to do about it.

A demand names the capability it is about. This reads the applications mapped
to that capability (the capability-to-application mapping the coverage lists
use -- no second coverage computation) and turns them into a recommendation.
The recommendation is advice: the person triaging records the route, and the
reasons shown at that moment are stored with it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.license_entitlement import LicenseEntitlement
from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
from app.models.unified_capability import UnifiedCapability

# A mapping counts as covering the capability when it is recorded as full
# support, or when its recorded coverage reaches this share.
FULL_COVERAGE_PERCENTAGE = 80


def visible_capabilities(limit: int = 500) -> List[UnifiedCapability]:
    """Capabilities the signed-in organisation can pick from (its own and shared)."""
    return list(
        db.session.execute(
            db.select(UnifiedCapability).order_by(UnifiedCapability.name).limit(limit)
        ).scalars()
    )


def coverage_for_capability(capability_id: int) -> List[Dict[str, Any]]:
    """Applications mapped to the capability, best coverage first.

    ``coverage_percentage`` is None when no figure was recorded, never 0.
    """
    rows = db.session.execute(
        db.select(ApplicationComponent, UnifiedApplicationCapabilityMapping)
        .join(
            UnifiedApplicationCapabilityMapping,
            UnifiedApplicationCapabilityMapping.application_component_id
            == ApplicationComponent.id,
        )
        .where(
            UnifiedApplicationCapabilityMapping.unified_capability_id == capability_id,
            UnifiedApplicationCapabilityMapping.is_active.is_(True),
        )
    ).all()
    out = []
    for app, mapping in rows:
        pct = mapping.coverage_percentage or None
        out.append(
            {
                "application_id": app.id,
                "name": app.name,
                "support_level": mapping.support_level,
                "coverage_percentage": pct,
            }
        )
    out.sort(key=lambda r: (-(r["coverage_percentage"] or 0), r["name"]))
    return out


def licences_for_applications(application_ids: List[int]) -> List[Dict[str, Any]]:
    """Licence entitlements recorded against the given applications.

    ``spare`` is entitled minus used, and None when usage was not recorded --
    an unmeasured licence is not shown as fully spare.
    """
    if not application_ids:
        return []
    rows = db.session.execute(
        db.select(LicenseEntitlement, ApplicationComponent)
        .join(ApplicationComponent, ApplicationComponent.id == LicenseEntitlement.application_id)
        .where(LicenseEntitlement.application_id.in_(application_ids))
        .order_by(ApplicationComponent.name, LicenseEntitlement.id)
    ).all()
    out = []
    for licence, app in rows:
        used = licence.quantity_used
        entitled = licence.quantity_entitled
        out.append(
            {
                "id": licence.id,
                "application_id": app.id,
                "application": app.name,
                "product": licence.product_name or app.name,
                "license_type": licence.license_type,
                "entitled": entitled,
                "used": used,
                "spare": (entitled - used) if (used is not None and entitled is not None) else None,
            }
        )
    return out


def _covers_fully(entry: Dict[str, Any]) -> bool:
    return entry["support_level"] == "full" or (entry["coverage_percentage"] or 0) >= FULL_COVERAGE_PERCENTAGE


def recommend(capability_id: Optional[int]) -> Dict[str, Any]:
    """A recommendation with the applications and the sentence behind it.

    ``route`` is ``reuse``, ``extend`` (something partly covers it) or
    ``buy_or_build`` (nothing is mapped); None when the demand names no
    capability, because there is nothing to read coverage from.
    """
    if capability_id is None:
        return {
            "route": None,
            "applications": [],
            "licences": [],
            "reason": "No capability is recorded on this demand, so existing coverage cannot be read.",
        }
    capability = db.session.execute(
        db.select(UnifiedCapability).where(UnifiedCapability.id == capability_id)
    ).scalar_one_or_none()
    if capability is None:
        return {
            "route": None,
            "applications": [],
            "licences": [],
            "reason": "The capability on this demand was not found.",
        }
    apps = coverage_for_capability(capability_id)
    licences = licences_for_applications([a["application_id"] for a in apps])
    names = ", ".join(a["name"] for a in apps[:5])
    full = [a for a in apps if _covers_fully(a)]
    if full:
        return {
            "route": "reuse",
            "applications": apps,
            "licences": licences,
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
            "licences": licences,
            "reason": "%s is only partly supported (%s); extending an existing application may be enough." % (
                capability.name, names),
        }
    return {
        "route": "buy_or_build",
        "applications": [],
        "licences": [],
        "reason": "No application is mapped to %s." % capability.name,
    }
