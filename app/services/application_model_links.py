"""What the architecture model already links to an application, by any relationship.

The application page reads this one list for the Architecture tab, for the
technology and supplier links the Technology and Vendors tabs offer to record,
and for the notes beside the completeness banner, so no two places on the
record can disagree about what is linked.

Only this organisation's elements and relationships are read: the application's
own organisation scopes both sides of every link.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app import db
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship


def normalised_type(element_type: Optional[str]) -> str:
    """"Business Actor", "business_actor" and "BusinessActor" read alike."""
    return (element_type or "").replace("_", "").replace(" ", "").lower()


def _links_by_element(org_id: int, element_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
    """The links of each given element, one row per relationship, in this organisation only."""
    rows: Dict[int, List[Dict[str, Any]]] = {element_id: [] for element_id in element_ids}
    if not element_ids:
        return rows
    for direction, near, far in (
        ("outgoing", ArchiMateRelationship.source_id, ArchiMateRelationship.target_id),
        ("incoming", ArchiMateRelationship.target_id, ArchiMateRelationship.source_id),
    ):
        found = db.session.execute(
            db.select(near, ArchiMateRelationship, ArchiMateElement)
            .join(ArchiMateElement, far == ArchiMateElement.id)
            .where(
                near.in_(element_ids),
                ArchiMateRelationship.organization_id == org_id,
                ArchiMateElement.organization_id == org_id,
            )
            .order_by(ArchiMateElement.name, ArchiMateRelationship.id)
        ).all()
        for near_id, relationship, element in found:
            rows[near_id].append({
                "id": element.id,
                "name": element.name,
                "type": element.type,
                "layer": (element.layer or "").title(),
                "rel_type": relationship.type,
                "relationship_id": relationship.id,
                "direction": direction,
            })
    return rows


def linked_elements(application: Any) -> List[Dict[str, Any]]:
    """Every element linked to the application's element, one row per relationship.

    Each row names the other element, its layer and type, the relationship's id and
    type, and ``direction``: ``outgoing`` when the application is the source.
    """
    element_id = getattr(application, "archimate_element_id", None)
    org_id = getattr(application, "organization_id", None)
    if not element_id or org_id is None:
        return []
    return _links_by_element(org_id, [element_id])[element_id]


def _once_per_element(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen, out = set(), []
    for row in rows:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        out.append(row)
    return out


def linked_technology(application: Any) -> List[Dict[str, Any]]:
    """Nodes and system software linked by anything other than a recorded "runs on".

    A recorded "runs on" is a realization from the technology element to the
    application; an element that has one is not listed here.
    """
    from app.modules.architecture.services.application_technology_links import (
        REALIZATION,
        TECHNOLOGY_TYPES,
    )

    rows = [r for r in linked_elements(application) if normalised_type(r["type"]) in TECHNOLOGY_TYPES]
    recorded = {
        r["id"] for r in rows
        if r["direction"] == "incoming" and (r["rel_type"] or "").lower() == REALIZATION
    }
    others = [r for r in rows if r["id"] not in recorded]
    return _once_per_element(others)


def recorded_technology(application: Any) -> List[Dict[str, Any]]:
    """Nodes and system software recorded as what the application runs on."""
    from app.modules.architecture.services.application_technology_links import (
        REALIZATION,
        TECHNOLOGY_TYPES,
    )

    return _once_per_element([
        r for r in linked_elements(application)
        if normalised_type(r["type"]) in TECHNOLOGY_TYPES
        and r["direction"] == "incoming" and (r["rel_type"] or "").lower() == REALIZATION
    ])


def _suppliers_among(rows: List[Dict[str, Any]], recorded_vendor_name: Optional[str]) -> List[Dict[str, Any]]:
    skip = " ".join((recorded_vendor_name or "").split()).lower()
    return _once_per_element([
        r for r in rows
        if normalised_type(r["type"]) == "businessactor"
        and " ".join((r["name"] or "").split()).lower() != skip
    ])


def linked_suppliers(application: Any, recorded_vendor_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Business actors linked to the application, other than the vendor already recorded."""
    return _suppliers_among(linked_elements(application), recorded_vendor_name)


def linked_suppliers_by_application(
    applications: List[Any], recorded_vendor_names: Optional[Dict[int, Optional[str]]] = None
) -> Dict[int, List[Dict[str, Any]]]:
    """``linked_suppliers`` for many applications of one organisation, in two queries.

    Keyed by application id; an application with no supplier linked has an empty list.
    ``recorded_vendor_names`` maps an application id to the vendor already recorded for it.
    """
    names = recorded_vendor_names or {}
    out: Dict[int, List[Dict[str, Any]]] = {a.id: [] for a in applications}
    by_org: Dict[int, List[Any]] = {}
    for application in applications:
        if getattr(application, "archimate_element_id", None) and application.organization_id is not None:
            by_org.setdefault(application.organization_id, []).append(application)
    for org_id, group in by_org.items():
        links = _links_by_element(org_id, list({a.archimate_element_id for a in group}))
        for application in group:
            out[application.id] = _suppliers_among(
                links[application.archimate_element_id], names.get(application.id))
    return out
