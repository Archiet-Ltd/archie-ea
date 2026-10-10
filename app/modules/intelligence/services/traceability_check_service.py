"""Traceability check: does one element trace up to a capability and down to
technology, where does each chain stop, and which missing relationships are
already known somewhere else.

The walk is the canonical one. Both directions are answered by
``IntelligenceQueryService.cross_layer_impact`` (explicit relationships only,
tenant-fenced, cycle-safe); this module never queries relationships to walk
them. It only reads the rows that walk returns:

* **Up** is the walk along relationships leaving the element (it realises,
  serves or is assigned to something), complete when a chain reaches a
  Capability.
* **Down** is the walk along relationships arriving at the element (something
  realises, serves or is assigned to it), complete when a chain reaches a
  technology or physical element.

Only structural relationship types count (realisation, serving, assignment,
composition, aggregation). A flow, trigger, access or association is not a
trace, so a chain continues only through the structural ones.

A chain that ends without reaching its target is reported with the element it
stops at. Candidate fixes are relationships an organisation already recorded
in a parallel table but never drew as a relationship: an application mapped to
a capability in the capability mapping tables, or an application linked to a
node or system software in the technology tables. A candidate is offered only
when both ends are elements in the caller's organisation, the pair is not
already related, and the relationship type is valid in ArchiMate 3.2 for the
two element types. Nothing is guessed from names.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from app.extensions import db
from app.middleware.tenant_context import current_org_id

TRACE_DEPTH = 4

# Relationship types that carry a trace. Everything else (flow, triggering,
# access, influence, association, specialization) is not followed.
TRACE_RELATIONSHIP_TYPES = frozenset({"realization", "serving", "assignment", "composition", "aggregation"})

UP_TARGET_TYPES = frozenset({"Capability"})
DOWN_TARGET_LAYERS = frozenset({"technology", "physical"})

# The relationship a candidate proposes, in order of preference; the first one
# valid for the two element types is offered. Serving comes first upward
# because it is what the capability mapping writer already records for a
# mapping it mirrors (the ApplicationCapabilityMapping listener).
_UP_CANDIDATE_TYPES = ("serving", "realization")
_DOWN_CANDIDATE_TYPES = ("serving", "realization", "assignment")

_ALIASES = {"realizes": "realization", "realisation": "realization", "serves": "serving", "used_by": "serving"}


def normalise_relationship_type(raw: Optional[str]) -> str:
    text = (raw or "").strip()
    if text.lower().endswith("relationship"):
        text = text[: -len("relationship")]
    text = text.strip().lower()
    return _ALIASES.get(text, text)


def _normalise_element_type(raw: Optional[str]) -> str:
    from app.services.archimate_validity_service import _normalize_type

    return _normalize_type(raw or "")


def _layer_of(element: Dict[str, Any]) -> Optional[str]:
    from app.services.archimate_validity_service import _TYPE_LAYER

    layer = element.get("layer")
    if layer:
        return str(layer).lower()
    return _TYPE_LAYER.get(_normalise_element_type(element.get("type")))


def _reaches_up(element: Dict[str, Any]) -> bool:
    return _normalise_element_type(element.get("type")) in UP_TARGET_TYPES


def _reaches_down(element: Dict[str, Any]) -> bool:
    return _layer_of(element) in DOWN_TARGET_LAYERS


def _trace_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep the rows reached through structural relationships only.

    The walk visits each element once, so every row's parent is the element
    before it in ``chain_elements``; a row is kept when its own relationship is
    structural and its parent is the root or a kept row.
    """
    ordered = sorted(rows, key=lambda r: r["relation"]["depth"])
    kept: Dict[int, Dict[str, Any]] = {}
    for row in ordered:
        relation = row["relation"]
        if normalise_relationship_type(relation.get("type")) not in TRACE_RELATIONSHIP_TYPES:
            continue
        chain = relation.get("chain_elements") or []
        if len(chain) < 2:
            continue
        parent = chain[-2]
        if relation["depth"] == 1 or parent in kept:
            kept[row["element_id"]] = row
    return list(kept.values())


def _describe(element_id: int, elements: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    entry = elements.get(str(element_id))
    if entry is None:
        return {"id": element_id, "name": None, "type": None, "layer": None}
    return {
        "id": entry["id"],
        "name": entry["name"],
        "type": entry["type"],
        "layer": _layer_of(entry),
    }


def _chains(
    root: Dict[str, Any],
    rows: List[Dict[str, Any]],
    elements: Dict[str, Dict[str, Any]],
    reaches,
) -> Tuple[bool, List[Dict[str, Any]]]:
    """Every chain from the root to a leaf, each marked complete or stopped.

    A chain is complete at the first element that meets the target; the
    elements after it are not part of the trace.
    """
    parents = {row["relation"]["chain_elements"][-2] for row in rows}
    leaves = [row for row in rows if row["element_id"] not in parents]
    chains: List[Dict[str, Any]] = []
    for leaf in sorted(leaves, key=lambda r: r["relation"]["chain_elements"]):
        path = [_describe(eid, elements) for eid in leaf["relation"]["chain_elements"]]
        path[0] = root
        end = None
        for index, element in enumerate(path[1:], start=1):
            if reaches(element):
                end = index
                break
        if end is not None:
            chains.append({"elements": path[: end + 1], "complete": True, "stops_at": None})
        else:
            chains.append({"elements": path, "complete": False, "stops_at": path[-1]})

    # Several leaves can share the same completed prefix; report it once.
    seen: Set[Tuple[int, ...]] = set()
    unique: List[Dict[str, Any]] = []
    for chain in chains:
        key = tuple(e["id"] for e in chain["elements"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(chain)

    if not unique:
        unique = [{"elements": [root], "complete": False, "stops_at": root}]
    return any(c["complete"] for c in unique), unique


def _walk(element_id: int, direction: str) -> Dict[str, Any]:
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    return IntelligenceQueryService.cross_layer_impact(
        element_id,
        include_derived=False,
        max_depth=TRACE_DEPTH,
        direction=direction,
        with_owner=False,
    )


class TraceabilityCheckService:
    """Read-only traceability check for one element in the caller's organisation."""

    @staticmethod
    def check(element_id: int, organization_id: Optional[int] = None) -> Dict[str, Any]:
        """The traceability answer for *element_id*.

        Returns ``{"state": "not_found"}`` when the element does not exist in
        the organisation (another organisation's element reads the same), else
        ``state: "checked"`` with the element, the ``up`` and ``down`` blocks
        (``complete``, ``chains``) and ``candidates``.
        """
        from app.models import ArchiMateElement

        org_id = organization_id if organization_id is not None else current_org_id()
        if org_id is None:
            return {"state": "no_organisation"}

        element = db.session.execute(
            db.select(ArchiMateElement.id, ArchiMateElement.name, ArchiMateElement.type, ArchiMateElement.layer).where(
                ArchiMateElement.id == element_id,
                ArchiMateElement.organization_id == org_id,
            )
        ).first()
        if element is None:
            return {"state": "not_found"}

        root = {
            "id": element.id,
            "name": element.name,
            "type": element.type,
            "layer": None,
        }
        root["layer"] = _layer_of({"type": element.type, "layer": str(element.layer) if element.layer else None})

        blocks: Dict[str, Dict[str, Any]] = {}
        related: Set[int] = set()
        for key, direction, reaches in (
            ("up", "downstream", _reaches_up),
            ("down", "upstream", _reaches_down),
        ):
            answer = _walk(element.id, direction)
            elements = answer.get("elements") or {}
            all_rows = answer.get("rows") or []
            related.update(r["element_id"] for r in all_rows if r["relation"]["depth"] == 1)
            rows = _trace_rows(all_rows)
            complete, chains = _chains(root, rows, elements, reaches)
            blocks[key] = {"complete": complete, "chains": chains}

        candidates = _parallel_table_candidates(
            root,
            org_id,
            want_up=not blocks["up"]["complete"],
            want_down=not blocks["down"]["complete"],
            already_related=related,
        )
        return {
            "state": "checked",
            "element": root,
            "up": blocks["up"],
            "down": blocks["down"],
            "candidates": candidates,
            "depth": TRACE_DEPTH,
        }


def _component_ids_for_element(element_id: int, org_id: int) -> List[int]:
    from app.models.application_portfolio import ApplicationComponent

    return list(
        db.session.execute(
            db.select(ApplicationComponent.id).where(
                ApplicationComponent.archimate_element_id == element_id,
                ApplicationComponent.organization_id == org_id,
            )
        ).scalars()
    )


def _capability_elements_from_mappings(component_ids: List[int], org_id: int) -> Set[int]:
    """Capability elements the capability mapping tables tie these applications to."""
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
    from app.models.unified_capability import UnifiedCapability

    found: Set[int] = set()
    if not component_ids:
        return found

    business_ids = [
        str(bid)
        for bid in db.session.execute(
            db.select(ApplicationCapabilityMapping.business_capability_id).where(
                ApplicationCapabilityMapping.application_component_id.in_(component_ids),
                ApplicationCapabilityMapping.organization_id == org_id,
            )
        ).scalars()
    ]
    if business_ids:
        found.update(
            db.session.execute(
                db.select(UnifiedCapability.archimate_element_id).where(
                    UnifiedCapability.source_table == "business_capability",
                    UnifiedCapability.source_id.in_(business_ids),
                    UnifiedCapability.organization_id == org_id,
                    UnifiedCapability.archimate_element_id.isnot(None),
                )
            ).scalars()
        )

    found.update(
        db.session.execute(
            db.select(UnifiedCapability.archimate_element_id)
            .join(
                UnifiedApplicationCapabilityMapping,
                UnifiedApplicationCapabilityMapping.unified_capability_id == UnifiedCapability.id,
            )
            .where(
                UnifiedApplicationCapabilityMapping.application_component_id.in_(component_ids),
                UnifiedCapability.organization_id == org_id,
                UnifiedCapability.archimate_element_id.isnot(None),
            )
        ).scalars()
    )
    return found


def _technology_elements_from_tables(component_ids: List[int], org_id: int) -> Set[int]:
    """Node and system software elements the technology tables tie these applications to."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.relationship_tables import ApplicationTechnologyMapping
    from app.models.technology_layer import Node, SystemSoftware

    found: Set[int] = set()
    if not component_ids:
        return found

    node_ids: Set[int] = set()
    software_ids: Set[int] = set()
    for node_id, software_id in db.session.execute(
        db.select(ApplicationTechnologyMapping.technology_node_id, ApplicationTechnologyMapping.system_software_id)
        .join(ApplicationComponent, ApplicationComponent.id == ApplicationTechnologyMapping.application_component_id)
        .where(
            ApplicationTechnologyMapping.application_component_id.in_(component_ids),
            ApplicationComponent.organization_id == org_id,
            db.or_(ApplicationTechnologyMapping.is_active.is_(None), ApplicationTechnologyMapping.is_active.is_(True)),
        )
    ).all():
        if node_id is not None:
            node_ids.add(node_id)
        if software_id is not None:
            software_ids.add(software_id)

    for model, ids in ((Node, node_ids), (SystemSoftware, software_ids)):
        conditions = [model.application_component_id.in_(component_ids)]
        if ids:
            conditions.append(model.id.in_(sorted(ids)))
        found.update(
            db.session.execute(
                db.select(model.archimate_element_id).where(
                    db.or_(*conditions),
                    model.organization_id == org_id,
                    model.archimate_element_id.isnot(None),
                )
            ).scalars()
        )
    return found


def _first_valid_type(source_type: str, target_type: str, preferred: Iterable[str]) -> Optional[str]:
    from app.services.archimate_validity_service import ArchimateValidityService

    validity = ArchimateValidityService()
    for rel_type in preferred:
        if validity.is_valid(source_type or "", target_type or "", rel_type):
            return rel_type
    return None


def _parallel_table_candidates(
    root: Dict[str, Any],
    org_id: int,
    *,
    want_up: bool,
    want_down: bool,
    already_related: Set[int],
) -> List[Dict[str, Any]]:
    from app.modules.intelligence.services.query_service import _resolve_elements_batch

    if not (want_up or want_down):
        return []
    component_ids = _component_ids_for_element(root["id"], org_id)
    if not component_ids:
        return []

    up_ids = _capability_elements_from_mappings(component_ids, org_id) if want_up else set()
    down_ids = _technology_elements_from_tables(component_ids, org_id) if want_down else set()
    up_ids.discard(root["id"])
    down_ids.discard(root["id"])
    # The identity map is the tenant fence: an id outside the organisation is
    # simply absent from it and never becomes a candidate.
    elements = _resolve_elements_batch(up_ids | down_ids, org_id)

    candidates: List[Dict[str, Any]] = []
    for eid in sorted(up_ids):
        target = elements.get(str(eid))
        if target is None or eid in already_related:
            continue
        rel_type = _first_valid_type(root["type"], target["type"], _UP_CANDIDATE_TYPES)
        if rel_type is None:
            continue
        candidates.append(
            {
                "direction": "up",
                "source": {"id": root["id"], "name": root["name"], "type": root["type"]},
                "target": {"id": target["id"], "name": target["name"], "type": target["type"]},
                "relationship_type": rel_type,
                "held_in": "capability mapping",
            }
        )
    for eid in sorted(down_ids):
        source = elements.get(str(eid))
        if source is None or eid in already_related:
            continue
        rel_type = _first_valid_type(source["type"], root["type"], _DOWN_CANDIDATE_TYPES)
        if rel_type is None:
            continue
        candidates.append(
            {
                "direction": "down",
                "source": {"id": source["id"], "name": source["name"], "type": source["type"]},
                "target": {"id": root["id"], "name": root["name"], "type": root["type"]},
                "relationship_type": rel_type,
                "held_in": "technology mapping",
            }
        )
    return candidates


__all__ = [
    "TraceabilityCheckService",
    "TRACE_DEPTH",
    "TRACE_RELATIONSHIP_TYPES",
    "normalise_relationship_type",
]
