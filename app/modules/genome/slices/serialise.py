"""Turn one registered slice into bounded, citeable model context (LG-1).

The serialiser never changes the builder's slice or its ``spec_hash``; it
returns a copy cut to a token budget, in a fixed order, and says what it cut:

1. apply ``focus_ids``, ``depth`` and the slice's declared ``filters``;
2. drop optional text fields in order: fenced excerpts, descriptions, then the
   slice's declared optional fields (identity, name, kind, status, dates,
   amounts, links and provenance are never dropped);
3. rank nodes by (hop distance from the focus, the slice's rank key, node id)
   and drop whole nodes from the tail, with every edge that names one;
4. never cut inside a value; set ``complete: false`` and
   ``truncated: {nodes_dropped, fields_dropped}``.

Tokens are estimated as serialised characters divided by four (AB-7).
"""
from __future__ import annotations

import copy
import json
from typing import Iterable, List, Optional

from .registry import SLICES

DEFAULT_BUDGET = 3000
MAX_BUDGET = 6000
TURN_BUDGET = 9000
MAX_FOCUS = 20
MAX_DEPTH = 2
_DROP_FIRST = ("excerpt", "description")
PARTIAL_NOTE = (
    "This answer covers part of your model: {n} items were left out; "
    "narrow the question to see them."
)


def estimate_tokens(value) -> int:
    return len(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)) // 4


def _hops(nodes: List[dict], edges: List[dict], focus: set, depth: int) -> dict:
    """Hop distance from the focus over the slice's own edges (the links the
    builder returned), at most ``depth`` hops. Nothing outside the slice is walked."""
    distance = {n["id"]: 0 for n in nodes if n["id"] in focus}
    frontier = set(distance)
    for hop in range(1, depth + 1):
        nxt = set()
        for edge in edges:
            a, b = edge.get("source"), edge.get("target")
            for here, there in ((a, b), (b, a)):
                if here in frontier and there not in distance:
                    distance[there] = hop
                    nxt.add(there)
        frontier = nxt
    return distance


def serialise(
    envelope: dict,
    *,
    focus_ids: Optional[Iterable] = None,
    depth: int = 1,
    filters: Optional[dict] = None,
    budget_tokens: int = DEFAULT_BUDGET,
) -> dict:
    """The model-facing copy of *envelope*, inside *budget_tokens*."""
    spec = SLICES[envelope["domain"]]
    budget = max(1, min(int(budget_tokens or DEFAULT_BUDGET), MAX_BUDGET))
    depth = max(0, min(int(depth if depth is not None else 1), MAX_DEPTH))
    nodes = copy.deepcopy(envelope["nodes"])
    edges = copy.deepcopy(envelope["edges"])

    # 1. Focus, depth and declared filters.
    distance = {}
    focus = {str(f) for f in (focus_ids or [])}
    if focus:
        by_element = {
            str((n.get("provenance") or {}).get("archimate_element_id")): n["id"] for n in nodes
        }
        focus = {by_element.get(f, f) for f in focus}
        distance = _hops(nodes, edges, focus, depth)
        nodes = [n for n in nodes if n["id"] in distance]
    for key, wanted in (filters or {}).items():
        if key in spec.filter_keys:
            nodes = [n for n in nodes if n.get(key) == wanted]
    kept_ids = {n["id"] for n in nodes}
    edges = [e for e in edges if e["source"] in kept_ids and e["target"] in kept_ids]

    out = {
        "domain": envelope["domain"],
        "slice_version": envelope["slice_version"],
        "spec_hash": envelope["spec_hash"],
        "organization_id": envelope["organization_id"],
        "store": envelope["store"],
        "as_of": envelope["as_of"],
        "reason": envelope.get("reason"),
        "nodes": nodes,
        "edges": edges,
        "complete": True,
        "truncated": {"nodes_dropped": 0, "fields_dropped": []},
        "budget_tokens": budget,
    }
    if estimate_tokens(out) <= budget:
        return out

    # 2. Optional text fields, in the fixed order.
    for name in _DROP_FIRST + tuple(spec.optional_fields):
        if not any(name in n for n in nodes):
            continue
        for n in nodes:
            n.pop(name, None)
        out["truncated"]["fields_dropped"].append(name)
        out["complete"] = False
        if estimate_tokens(out) <= budget:
            return out

    # 3. Whole nodes from the tail of the ranking.
    def rank(n):
        return (distance.get(n["id"], 0), str(n.get(spec.rank_key) or "").lower(), n["id"])

    nodes.sort(key=rank)
    dropped = 0
    while nodes and estimate_tokens(out) > budget:
        gone = nodes.pop()
        dropped += 1
        out["edges"] = [e for e in out["edges"] if gone["id"] not in (e["source"], e["target"])]
    out["truncated"]["nodes_dropped"] = dropped
    out["complete"] = False
    return out
