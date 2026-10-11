"""Tech Radar service (ARCH-124).

A tech radar classifies technology that is already modelled — it is not a
new inventory. The candidate set is every Technology-layer ArchiMateElement
that already exists (created automatically from a real Node, Device,
SystemSoftware or TechnologyService record — see
app/models/technology_layer.py's before_insert listeners). This module only
adds the adopt/trial/assess/hold classification on top.

Nothing here invents a ring. An element with no TechRadarEntry is
"not yet classified" and is rendered as such, never defaulted onto a ring.
"""

from __future__ import annotations

from typing import Dict, List

from app import db
from app.models.archimate_core import ArchiMateElement
from app.models.tech_radar import RADAR_RINGS, TechRadarEntry

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
