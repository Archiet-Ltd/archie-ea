"""Derivation coverage report for one organisation.

Composes the existing readers -- ``derived_facts`` and the graph projection --
into elements per ArchiMate layer, the derived relationship count, the latest
run's yield, and the bounded list of elements with no derived relationship.

No new SQL over the element or relationship tables: layer counts come from the
projection's own nodes, derived relationships come from ``list_derived_facts``
(the single read path over the derived-fact store), and the run yield comes
from ``latest_derivation_run``. Every figure is filtered by the caller's
organisation and read from stored rows; nothing is fabricated -- an
organisation with no completed run shows a dash for its yield.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.modules.intelligence.services.derived_facts import (
    derived_fact_aggregates,
    latest_derivation_run,
    list_derived_facts,
)
from app.modules.intelligence.services.graph_projection import get_projection

# The missing list is bounded so a thin layer never opens an unbounded page.
MISSING_LIMIT = 50


def derivation_coverage(
    organization_id: int, *, layer: Optional[str] = None
) -> Dict[str, Any]:
    """Coverage figures for one organisation, all read from stored rows.

    ``layer``, when given, restricts the missing list to that layer and counts
    only its elements. Returns ``latest_run`` as ``None`` when derivation has
    never completed for the organisation -- the template renders that as a
    dash, never as zero.
    """
    organization_id = int(organization_id)
    projection = get_projection(organization_id)
    if projection is None:
        # No projection: return empty structure with latest_run from readers
        latest_run = latest_derivation_run(organization_id)
        return {
            "organization_id": organization_id,
            "layers": [],
            "derived_count": 0,
            "latest_run": latest_run,
            "missing_total": 0,
            "missing": [],
        }
    nodes = projection.nodes()
    if not nodes:
        # Projection with no nodes: return empty structure
        latest_run = latest_derivation_run(organization_id)
        return {
            "organization_id": organization_id,
            "layers": [],
            "derived_count": 0,
            "latest_run": latest_run,
            "missing_total": 0,
            "missing": [],
        }

    by_layer: Dict[str, int] = {}
    missing_by_layer: Dict[str, int] = {}
    for element_id, _type, node_layer in nodes:
        if layer is not None and node_layer != layer:
            continue
        key = node_layer or ""
        by_layer[key] = by_layer.get(key, 0) + 1

    aggregates = derived_fact_aggregates(organization_id)
    latest_run = latest_derivation_run(organization_id)

    facts = list_derived_facts(organization_id)
    derived_element_ids: set[int] = set()
    for fact in facts:
        derived_element_ids.add(int(fact["source_element_id"]))
        derived_element_ids.add(int(fact["target_element_id"]))

    missing: List[Tuple[int, Optional[str], Optional[str]]] = []
    for element_id, type_, node_layer in sorted(nodes):
        if element_id in derived_element_ids:
            continue
        if layer is not None and node_layer != layer:
            continue
        missing_by_layer[node_layer or ""] = missing_by_layer.get(node_layer or "", 0) + 1
        missing.append((element_id, type_, node_layer))

    missing_total = len(missing)
    missing_shown = missing[:MISSING_LIMIT]

    return {
        "organization_id": organization_id,
        "layers": [
            {
                "layer": node_layer,
                "element_count": by_layer.get(node_layer, 0),
                "missing_count": missing_by_layer.get(node_layer, 0),
            }
            for node_layer in sorted(by_layer)
        ],
        "derived_count": int(aggregates["derived_count"]),
        "latest_run": latest_run,
        "missing_total": missing_total,
        "missing": missing_shown,
    }


__all__ = ["derivation_coverage", "MISSING_LIMIT"]
