"""What-if comparison: what stops if each of up to three elements stops.

Retiring an element, losing a supplier and an outage are the same question for
the model: what is connected downstream of this element. The answer therefore
comes from the one impact walk (``IntelligenceQueryService.cross_layer_impact``)
and nothing is computed beyond counting its rows. No scenario is stored and no
change is made to the model; the elements chosen are carried in the address.
"""
from __future__ import annotations

from typing import Any, Dict, List

MAX_SCENARIOS = 3

LAYER_ORDER = ("motivation", "strategy", "business", "application", "technology", "physical", "implementation")


def _order_layers(counts: Dict[str, int]) -> List[Dict[str, Any]]:
    known = [{"layer": name, "count": counts[name]} for name in LAYER_ORDER if name in counts]
    other = [{"layer": name, "count": c} for name, c in sorted(counts.items()) if name not in LAYER_ORDER]
    return known + other


def _scenario(element_id: int) -> Dict[str, Any]:
    from app.modules.intelligence.services.query_service import (
        ELEMENT_NOT_FOUND_REASON,
        IntelligenceQueryService,
    )

    answer = IntelligenceQueryService.cross_layer_impact(
        element_id, direction="downstream", max_depth=3, with_owner=True
    )
    if ELEMENT_NOT_FOUND_REASON in (answer.get("reasons") or []):
        return {"element_id": element_id, "found": False}

    elements = answer.get("elements") or {}
    root = elements.get(str(element_id)) or {}
    seen: Dict[int, Dict[str, Any]] = {}
    for row in answer.get("rows") or []:
        eid = row["element_id"]
        if eid == element_id:
            continue
        depth = row["relation"]["depth"]
        known = elements.get(str(eid), {})
        entry = seen.get(eid)
        if entry is None or depth < entry["depth"]:
            owner = row.get("owner") or {}
            seen[eid] = {
                "element_id": eid,
                "name": known.get("name"),
                "type": known.get("type"),
                "layer": known.get("layer"),
                "depth": depth,
                "owner": owner.get("name"),
            }
    affected = sorted(seen.values(), key=lambda a: (a["depth"], (a["name"] or "").lower(), a["element_id"]))
    layers: Dict[str, int] = {}
    for a in affected:
        key = a["layer"] or "unclassified"
        layers[key] = layers.get(key, 0) + 1
    return {
        "element_id": element_id,
        "found": True,
        "name": root.get("name"),
        "type": root.get("type"),
        "layer": root.get("layer"),
        "affected": affected,
        "direct_count": sum(1 for a in affected if a["depth"] == 1),
        "capability_count": sum(1 for a in affected if a["type"] == "Capability"),
        "layers": _order_layers(layers),
        "derivation_state": (answer.get("summary") or {}).get("derivation_state"),
    }


def compare(element_ids: List[int]) -> Dict[str, Any]:
    """Run the impact walk for each chosen element and find what they share."""
    chosen: List[int] = []
    for eid in element_ids:
        if eid not in chosen:
            chosen.append(eid)
    truncated = len(chosen) > MAX_SCENARIOS
    scenarios = [_scenario(eid) for eid in chosen[:MAX_SCENARIOS]]

    found = [s for s in scenarios if s["found"]]
    shared: List[Dict[str, Any]] = []
    if len(found) > 1:
        counts: Dict[int, int] = {}
        for s in found:
            for a in s["affected"]:
                counts[a["element_id"]] = counts.get(a["element_id"], 0) + 1
        first = {a["element_id"]: a for s in found for a in s["affected"]}
        shared = sorted(
            (first[eid] for eid, n in counts.items() if n > 1),
            key=lambda a: ((a["name"] or "").lower(), a["element_id"]),
        )
    return {"scenarios": scenarios, "shared": shared, "truncated": truncated, "max": MAX_SCENARIOS}
