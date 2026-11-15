"""Impact / dependency graph — the blast-radius lens for one ArchiMate element.

An architect's hardest recurring question is "if I change or retire this, what
breaks?". The data to answer it is already in the ArchiMate relationship graph;
what was missing was a way to *see* it. This service walks that graph outward
from a chosen element — both directions, a bounded number of hops — and returns
the nodes and edges a node-link view renders, so the blast radius is something
you click through rather than infer.

Reads only. Tenant scoping comes from the ORM (ArchiMateRelationship /
ArchiMateElement carry TenantMixin, so queries inside a request are already
constrained to the caller's organisation).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app import db

# Guardrail: a hub element can reach thousands of others. Past this the picture
# stops being legible and starts being a hairball, so we cap and say so.
_MAX_NODES = 160

# Which end of an ArchiMate relationship depends on the other. An arrow's
# source is not always the dependent: in "A serves B" it is B that depends on
# A, so a change to A reaches B. Reading every outgoing edge as "depends on"
# got serving - the commonest application relationship - backwards.
#   target depends on source: the source serves, realises, is assigned to,
#       triggers, flows into or influences the target;
#   source depends on target: the source accesses, is composed of, aggregates
#       or specialises the target.
# Association carries no dependency either way.
_TARGET_DEPENDS_ON_SOURCE = frozenset({
    "serving", "usedby", "realization", "assignment", "triggering", "flow", "influence",
})
_SOURCE_DEPENDS_ON_TARGET = frozenset({
    "access", "composition", "aggregation", "specialization",
})
_ALIASES = {
    "serves": "serving", "uses": "serving", "realizes": "realization",
    "realisation": "realization", "realises": "realization", "assigns": "assignment",
    "triggers": "triggering", "flows": "flow", "composes": "composition",
    "aggregates": "aggregation", "specializes": "specialization",
    "specialisation": "specialization", "accesses": "access", "influences": "influence",
}


def _canonical_type(rel_type: Optional[str]) -> str:
    raw = re.sub(r"(?i)relationship$", "", (rel_type or "").strip())
    key = re.sub(r"[^a-z]", "", raw.lower())
    return _ALIASES.get(key, key)


def canonical_relationship_type(rel_type: Optional[str]) -> str:
    """The lower-case name of a relationship type, whichever spelling it was stored in."""
    return _canonical_type(rel_type)


def dependency_ends(rel_type: Optional[str], source_id: int, target_id: int) -> Optional[Tuple[int, int]]:
    """``(dependent_id, provider_id)`` for one relationship, or None.

    None for association and for types that carry no dependency direction.
    """
    kind = _canonical_type(rel_type)
    if kind in _TARGET_DEPENDS_ON_SOURCE:
        return target_id, source_id
    if kind in _SOURCE_DEPENDS_ON_TARGET:
        return source_id, target_id
    return None


def _dependency_chain(element_id: int, depth: int, want: str, org_id: Optional[int] = None) -> Dict[int, int]:
    """Elements that depend on ``element_id`` (want='consumers') or that it
    depends on (want='providers'), following only consistent dependency
    edges, with the fewest hops to each. A sibling that merely shares a
    provider is not a consumer of the centre and is not returned.

    Only elements in ``org_id`` are walked: a dependency edge into another
    organisation stops at the boundary.
    """
    from app.models.archimate_core import ArchiMateRelationship  # noqa: PLC0415
    from app.modules.intelligence.services.query_service import (  # noqa: PLC0415
        _resolve_elements_batch,
    )

    hops: Dict[int, int] = {}
    level = [element_id]
    dist = 0
    while level and dist < depth:
        rels = ArchiMateRelationship.query.filter(
            db.or_(ArchiMateRelationship.source_id.in_(level),
                   ArchiMateRelationship.target_id.in_(level))
        ).order_by(ArchiMateRelationship.id).all()
        reached: List[int] = []
        for cur_id in level:
            for r in rels:
                if cur_id not in (r.source_id, r.target_id):
                    continue
                ends = dependency_ends(r.type, r.source_id, r.target_id)
                if ends is None:
                    continue
                dependent, provider = ends
                if want == "consumers" and provider == cur_id:
                    nxt = dependent
                elif want == "providers" and dependent == cur_id:
                    nxt = provider
                else:
                    continue
                if nxt == element_id or nxt in hops or nxt in reached:
                    continue
                reached.append(nxt)
        if org_id is not None and reached:
            # One fenced query for the whole level, through the shared batched helper.
            inside = _resolve_elements_batch(reached, org_id)
            reached = [n for n in reached if str(n) in inside]
        dist += 1
        for nxt in reached:
            hops[nxt] = dist
        level = reached
    return hops


def _layer_of(el: Dict[str, Any]) -> str:
    return (el.get("layer") or "other").lower()


def build_impact_graph(element_id: int, depth: int = 2) -> Optional[Dict[str, Any]]:
    """Breadth-first blast radius around ``element_id`` to ``depth`` hops.

    Returns ``{center, nodes, edges, truncated, counts}`` or None if the element
    does not exist / is not visible to this tenant.

    Each node carries its shortest ``distance`` from the centre and a
    ``direction`` — 'downstream' (this element depends on it), 'upstream'
    (it depends on this element), 'both', or 'related' (connected, but
    through no consistent chain of dependencies). Edges keep the ArchiMate
    relationship ``type`` and their real source→target orientation.

    ``consumers`` and ``providers`` list, nearest first, every element that
    depends on the centre and every element the centre depends on within
    ``depth`` hops, each with its ``hops`` along that dependency chain (1 is
    direct).
    """
    from app.models.archimate_core import (  # noqa: PLC0415
        ArchiMateElement, ArchiMateRelationship,
    )
    from app.modules.intelligence.services.query_service import (  # noqa: PLC0415
        _resolve_elements_batch,
    )

    depth = max(1, min(int(depth or 2), 4))
    center = db.session.get(ArchiMateElement, element_id)
    if center is None:
        return None

    center_org_id = getattr(center, "organization_id", None)

    nodes: Dict[int, Dict[str, Any]] = {}
    edges: Dict[tuple, Dict[str, Any]] = {}
    truncated = False

    def _add_node(el, distance: int, direction: str):
        el_id = el["id"]
        n = nodes.get(el_id)
        if n is None:
            nodes[el_id] = {
                "id": el_id,
                "name": el.get("name") or f"Element {el_id}",
                "type": el.get("type"),
                "layer": _layer_of(el),
                "distance": distance,
                "direction": direction,
                "is_center": el_id == element_id,
            }
        else:
            n["distance"] = min(n["distance"], distance)
            if not n["is_center"] and n["direction"] != direction:
                n["direction"] = "both"

    _add_node({"id": center.id, "name": center.name, "type": center.type, "layer": center.layer}, 0, "center")

    # Breadth-first, one level at a time: every far end of a level is fenced in
    # one query through the shared batched helper.
    seen: Set[int] = {element_id}
    level = [element_id]
    dist = 0
    while level and dist < depth:
        out_rels = ArchiMateRelationship.query.filter(
            ArchiMateRelationship.source_id.in_(level)).order_by(ArchiMateRelationship.id).all()
        in_rels = ArchiMateRelationship.query.filter(
            ArchiMateRelationship.target_id.in_(level)).order_by(ArchiMateRelationship.id).all()
        inside = _resolve_elements_batch(
            [r.target_id for r in out_rels] + [r.source_id for r in in_rels], center_org_id)
        next_level: List[int] = []
        for cur_id in level:
            steps = ([(r, r.target_id, "downstream") for r in out_rels if r.source_id == cur_id]
                     + [(r, r.source_id, "upstream") for r in in_rels if r.target_id == cur_id])
            for r, far_id, direction in steps:
                other = inside.get(str(far_id))
                if other is None:
                    continue
                _add_node(other, dist + 1, direction)
                edges.setdefault((r.source_id, r.target_id, r.type),
                                 {"source": r.source_id, "target": r.target_id,
                                  "type": r.type})
                if far_id not in seen:
                    if len(nodes) >= _MAX_NODES:
                        truncated = True
                        continue
                    seen.add(far_id)
                    next_level.append(far_id)
        level = next_level
        dist += 1

    consumer_hops = _dependency_chain(element_id, depth, "consumers", center_org_id)
    provider_hops = _dependency_chain(element_id, depth, "providers", center_org_id)
    for node_id, node in nodes.items():
        if node["is_center"]:
            continue
        up, down = node_id in consumer_hops, node_id in provider_hops
        node["direction"] = "both" if up and down else "upstream" if up else "downstream" if down else "related"

    def _listed(hops: Dict[int, int]) -> List[Dict[str, Any]]:
        found = _resolve_elements_batch(list(hops), center_org_id)
        out = []
        for el_id, n_hops in hops.items():
            el = found.get(str(el_id))
            if el is None:
                continue
            out.append({"id": el_id, "name": el["name"] or f"Element {el_id}",
                        "type": el["type"], "layer": _layer_of(el), "hops": n_hops})
        return sorted(out, key=lambda item: (item["hops"], item["name"].lower()))

    consumers = _listed(consumer_hops)
    providers = _listed(provider_hops)
    node_list: List[Dict[str, Any]] = list(nodes.values())

    return {
        "center": nodes[element_id],
        "nodes": node_list,
        "edges": list(edges.values()),
        "consumers": consumers,
        "providers": providers,
        "truncated": truncated,
        "depth": depth,
        "counts": {
            "total": len(node_list) - 1,
            "upstream": len(consumers),
            "downstream": len(providers),
        },
    }
