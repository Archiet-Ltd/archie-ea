"""Tech Radar service (ARCH-124).

A tech radar classifies technology that is already modelled — it is not a
new inventory. The candidate set is every Technology-layer ArchiMateElement
that already exists (created automatically from a real Node, Device,
SystemSoftware or TechnologyService record — see
app/models/technology_layer.py's before_insert listeners). This module only
adds the adopt/trial/assess/hold classification on top.

Nothing here invents a ring. An element with no TechRadarEntry is
"not yet classified" and is rendered as such, never defaulted onto a ring.

Sunsetting a standard (``sunset``) moves its entry to the hold ring with a
sunset date and an optional replacement, then tells the owners of every
application running it. "Running it" is answered by the one impact engine
(IntelligenceQueryService.cross_layer_impact) over the modelled relationships,
not by a second walk here; owners are the recorded ApplicationOwner users.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app import db
from app.models.archimate_core import ArchiMateElement
from app.models.tech_radar import RADAR_RING_LABELS, RADAR_RINGS, TechRadarEntry

logger = logging.getLogger(__name__)

# How far from the technology the impact walk looks for applications. Two hops
# covers "application <- node <- system software" (a database version on a
# server an application runs on) without reaching applications that merely
# talk to one that does: a chain passing through another application-layer
# element is not "running on" this technology and is dropped below.
_RUNS_ON_DEPTH = 2

NO_APPLICATIONS_REASON = (
    "No application is recorded as running on this technology: no relationship "
    "links an application to it in the architecture model."
)
NO_OWNER_REASON = "No owner is recorded for this application."

# Marks a classify() argument the caller did not send, so a ring change made
# from the radar's "Move" control leaves the recorded details alone.
UNCHANGED = object()


def technology_candidates() -> List[ArchiMateElement]:
    """Every Technology-layer ArchiMateElement in the current tenant —
    the real, already-modelled estate a radar classifies."""
    return (
        ArchiMateElement.query.filter(ArchiMateElement.layer == "Technology")
        .order_by(ArchiMateElement.type, ArchiMateElement.name)
        .all()
    )


def radar_state() -> Dict:
    """Build the radar: classified entries grouped by ring, plus the
    unclassified remainder. Returns real data only — an empty candidate
    set or an empty classification set renders as an explicit empty state,
    not a fabricated demo radar."""
    candidates = technology_candidates()
    entries = {
        e.archimate_element_id: e
        for e in TechRadarEntry.query.filter(
            TechRadarEntry.archimate_element_id.in_([c.id for c in candidates])
        ).all()
    } if candidates else {}

    # "Same purpose" is answered only as far as the model records it: the
    # other technology elements of the same ArchiMate element type (another
    # System Software for a System Software, another Node for a Node). The
    # model holds no finer purpose than that, so nothing finer is claimed.
    by_type: Dict[str, List[Dict]] = {}
    for element in candidates:
        entry = entries.get(element.id)
        by_type.setdefault(element.type or "", []).append(
            {"id": element.id, "name": element.name, "ring": entry.ring if entry else None}
        )

    def _peers(element):
        return [p for p in by_type.get(element.type or "", []) if p["id"] != element.id]

    rings: Dict[str, List[Dict]] = {r: [] for r in RADAR_RINGS}
    unclassified: List[ArchiMateElement] = []
    unclassified_peers: Dict[int, List[Dict]] = {}
    for element in candidates:
        entry = entries.get(element.id)
        if entry is None:
            unclassified.append(element)
            unclassified_peers[element.id] = _peers(element)
        else:
            rings[entry.ring].append(
                {"element": element, "entry": entry, "same_type": _peers(element)}
            )

    return {
        "total_candidates": len(candidates),
        "candidates": candidates,
        "rings": rings,
        "unclassified": unclassified,
        "unclassified_peers": unclassified_peers,
        "classified_count": len(entries),
    }


def rings_for_elements(element_ids) -> Dict[int, str]:
    """The recorded ring of each given element that has one, in this tenant."""
    ids = [i for i in element_ids if i]
    if not ids:
        return {}
    return {
        e.archimate_element_id: e.ring
        for e in TechRadarEntry.query.filter(TechRadarEntry.archimate_element_id.in_(ids)).all()
    }


def classify(
    archimate_element_id: int,
    ring: str,
    rationale,
    user_id: int,
    *,
    review_date=UNCHANGED,
    requesting_initiative_id=UNCHANGED,
    commit: bool = True,
) -> TechRadarEntry:
    """Set the ring of one technology element, with its recorded details.

    ``rationale``, ``review_date`` and ``requesting_initiative_id`` are only
    written when given; ``None`` for ``rationale`` or ``UNCHANGED`` for the
    others leaves what is already recorded. An initiative must be one this
    tenant can read.
    """
    if ring not in RADAR_RINGS:
        raise ValueError(f"ring must be one of {RADAR_RINGS}")

    if requesting_initiative_id not in (UNCHANGED, None):
        from app.models.strategic import StrategicInitiative

        found = StrategicInitiative.query.filter(
            StrategicInitiative.id == requesting_initiative_id
        ).first()
        if found is None:
            raise ValueError("not an initiative in this organisation")

    # Not .query.get(): on an identity-map HIT it returns the cached object
    # without emitting SQL, so do_orm_execute never runs and no tenant predicate
    # is applied (CLAUDE.md). An explicit filter() always emits the query, which
    # is what makes "in this tenant" above a guarantee rather than a side effect
    # of the session happening to be cold.
    element = ArchiMateElement.query.filter(
        ArchiMateElement.id == archimate_element_id
    ).first()
    if element is None or element.layer != "Technology":
        raise ValueError("not a technology-layer element in this tenant")

    entry = TechRadarEntry.query.filter_by(archimate_element_id=archimate_element_id).first()
    if entry is None:
        entry = TechRadarEntry(archimate_element_id=archimate_element_id)
        db.session.add(entry)
    entry.ring = ring
    if rationale is not None:
        entry.rationale = (rationale or "").strip() or None
    if review_date is not UNCHANGED:
        entry.review_date = review_date
    if requesting_initiative_id is not UNCHANGED:
        entry.requesting_initiative_id = requesting_initiative_id
    entry.set_by_user_id = user_id
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return entry


# --------------------------------------------------------------------- #
# Sunsetting a standard                                                  #
# --------------------------------------------------------------------- #


def _technology_element(element_id: int) -> Optional[ArchiMateElement]:
    """A technology-layer element in the current tenant, or None. filter(),
    not .get(), so the tenant predicate always runs (see classify)."""
    element = ArchiMateElement.query.filter(ArchiMateElement.id == element_id).first()
    if element is None or element.layer != "Technology":
        return None
    return element


def affected_applications(element_id: int) -> Dict[str, Any]:
    """Applications running on a technology element, each with its owners.

    Returns ``{"applications": [...], "reason": str|None}``. Each application is
    ``{"id", "name", "owners": [{"user_id", "name", "email", "ownership_type"}],
    "owner_reason": str|None, "path": [element names]}``. An empty list carries
    the reason it is empty; an application with no recorded owner carries
    NO_OWNER_REASON rather than an invented owner.
    """
    from app.middleware.tenant_context import current_org_id
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.models.user import User
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org_id = current_org_id()
    if org_id is None:
        return {"applications": [], "reason": "No organisation is selected."}

    answer = IntelligenceQueryService.cross_layer_impact(
        element_id,
        include_derived=False,
        max_depth=_RUNS_ON_DEPTH,
        direction="both",
        with_owner=False,
    )
    elements = answer.get("elements") or {}

    def _layer(eid):
        return ((elements.get(str(eid)) or {}).get("layer") or "").lower()

    reached: Dict[int, List[int]] = {}
    for row in answer.get("rows") or []:
        chain = (row.get("relation") or {}).get("chain_elements") or []
        # Everything between the technology and the application must itself be
        # technology: a chain through another application is not "runs on".
        if any(_layer(eid) != "technology" for eid in chain[1:-1]):
            continue
        reached.setdefault(row["element_id"], chain)

    if not reached:
        return {"applications": [], "reason": NO_APPLICATIONS_REASON}

    components = (
        ApplicationComponent.query.filter(
            ApplicationComponent.archimate_element_id.in_(list(reached))
        )
        .order_by(ApplicationComponent.name, ApplicationComponent.id)
        .all()
    )
    if not components:
        return {"applications": [], "reason": NO_APPLICATIONS_REASON}

    component_ids = [c.id for c in components]
    owner_rows = (
        db.session.query(ApplicationOwner, User)
        .join(User, User.id == ApplicationOwner.user_id)
        .filter(
            ApplicationOwner.application_id.in_(component_ids),
            ApplicationOwner.organization_id == org_id,
            User.organization_id == org_id,
        )
        .order_by(ApplicationOwner.application_id, ApplicationOwner.ownership_type, User.id)
        .all()
    )
    owners_by_app: Dict[int, List[Dict[str, Any]]] = {}
    for owner, user in owner_rows:
        full_name = " ".join(
            part for part in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if part
        )
        owners_by_app.setdefault(owner.application_id, []).append({
            "user_id": user.id,
            "name": full_name or user.email,
            "email": user.email,
            "ownership_type": owner.ownership_type,
        })

    applications = []
    seen = set()
    for comp in components:
        if comp.id in seen:
            continue
        seen.add(comp.id)
        owners = owners_by_app.get(comp.id, [])
        chain = reached.get(comp.archimate_element_id) or []
        applications.append({
            "id": comp.id,
            "name": comp.name,
            "owners": owners,
            "owner_reason": None if owners else NO_OWNER_REASON,
            "path": [
                (elements.get(str(eid)) or {}).get("name") or "\u2014" for eid in chain
            ],
        })
    return {"applications": applications, "reason": None}


def sunset(
    archimate_element_id: int,
    sunset_date: date,
    replacement_element_id: Optional[int],
    rationale: str,
    user_id: int,
    notify_url: str,
) -> Dict[str, Any]:
    """Move a technology to the hold ring with a sunset date and replacement,
    and notify the owners of every application running it.

    Returns ``{"entry", "affected", "notified"}``. Raises ValueError for an
    element or replacement that is not a technology in this tenant.
    """
    element = _technology_element(archimate_element_id)
    if element is None:
        raise ValueError("not a technology-layer element in this tenant")
    if not isinstance(sunset_date, date):
        raise ValueError("a sunset date is required")

    replacement = None
    if replacement_element_id:
        if replacement_element_id == archimate_element_id:
            raise ValueError("a technology cannot replace itself")
        replacement = _technology_element(replacement_element_id)
        if replacement is None:
            raise ValueError("the replacement is not a technology-layer element in this tenant")

    entry = TechRadarEntry.query.filter_by(archimate_element_id=archimate_element_id).first()
    if entry is None:
        entry = TechRadarEntry(archimate_element_id=archimate_element_id)
        db.session.add(entry)
    entry.ring = "hold"
    entry.sunset_date = sunset_date
    entry.replacement_element_id = replacement.id if replacement else None
    if (rationale or "").strip():
        entry.rationale = rationale.strip()
    entry.set_by_user_id = user_id
    db.session.flush()

    affected = affected_applications(archimate_element_id)
    user_ids = sorted({
        owner["user_id"]
        for app_row in affected["applications"]
        for owner in app_row["owners"]
    })

    notified = {"notified_users": 0, "emailed": 0}
    if user_ids:
        from app.modules.solutions_strategic.v2.services.governance_notifier import (
            GovernanceNotifier,
        )

        message = "%s moves to %s: sunset on %s.%s An application you own runs on it." % (
            element.name,
            RADAR_RING_LABELS["hold"],
            sunset_date.strftime("%d %b %Y"),
            (" Replace it with %s." % replacement.name) if replacement else "",
        )
        notified = GovernanceNotifier.notify_users(
            user_ids,
            message,
            notify_url,
            subject="Technology sunset: %s" % element.name,
        )

    entry.owners_notified_at = datetime.utcnow()
    entry.owners_notified_count = notified.get("notified_users", 0)
    db.session.commit()
    return {"entry": entry, "affected": affected, "notified": notified}


def sunset_entries() -> List[Dict[str, Any]]:
    """Every hold-ring entry with a sunset date, each with the applications
    running it and their owners, read live -- what the radar page lists."""
    entries = (
        TechRadarEntry.query.filter(
            TechRadarEntry.ring == "hold", TechRadarEntry.sunset_date.isnot(None)
        )
        .order_by(TechRadarEntry.sunset_date, TechRadarEntry.id)
        .all()
    )
    return [
        {"entry": entry, "affected": affected_applications(entry.archimate_element_id)}
        for entry in entries
    ]
