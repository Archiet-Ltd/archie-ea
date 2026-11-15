"""The model map: every element of the organisation's model, and the links between them, for one force graph.

Reads the elements and relationships through the ORM, so the organisation filter the tenant middleware
installs applies to both reads. Each node carries where it came from (its origin and the date it was
recorded), worded by the one result-footer service, and the address of that element's own page.

Above ``MAX_NODES`` elements the map is drawn one node per layer with its count; asking for a single
layer returns that layer's elements, capped at the same number and marked as capped.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from flask import url_for

from app import db
from app.services import result_footer_service as footer

MAX_NODES = 2000

# The layer set is the one count_layers counts: Physical's four types are Technology's, and a type no layer holds
# is "other" (count_layers calls that row "composite").
LAYERS = ("strategy", "motivation", "business", "application", "technology", "implementation", "other")
LAYER_LABELS = {
    "strategy": "Strategy", "motivation": "Motivation", "business": "Business", "application": "Application",
    "technology": "Technology", "implementation": "Implementation and migration", "other": "Other",
}
# The model pages are laid out under these layers; a layer they hold no pages for opens under Business.
_PAGE_LAYERS = frozenset(LAYERS) - {"other"}


def layer_key(stored_layer: Optional[str], element_type: Optional[str]) -> str:
    """The layer an element is drawn in, by the same rule count_layers reads: the layer it is stored in."""
    from app.services import archimate_viewpoint_service as counting

    layer = counting.element_layer(stored_layer, element_type)
    return "other" if layer == counting.COMPOSITE_LAYER else layer


def _counting_layer(key: str) -> str:
    from app.services.archimate_viewpoint_service import COMPOSITE_LAYER

    return COMPOSITE_LAYER if key == "other" else key


def element_href(element_id: int, layer: str, element_type: Optional[str]) -> str:
    """The element's own record page (``ae=1``: the id is the model element's id, not a dedicated table's)."""
    page_layer = layer if layer in _PAGE_LAYERS else "business"
    return url_for("archimate_crud.detail_element", layer=page_layer, element_type=element_type or "Element",
                   element_id=element_id, ae=1)


def _day(value) -> Optional[str]:
    return "%d %s %d" % (value.day, value.strftime("%b"), value.year) if value is not None else None


def origin_sentence(source_kind: Optional[str], source_label: Optional[str], source_ref: Optional[str]) -> str:
    """Where one element came from, in the words every result footer uses. An element with no recorded origin
    says it was entered by hand, naming the person when the record does."""
    kind = source_kind or "manual"
    label = source_label
    if kind == "connector":
        label = footer._connector_label({"source_label": source_label, "source_ref": source_ref})
    return footer._origin_text(footer.fact("", source_kind=kind, source_label=label))


def layer_counts() -> List[Dict[str, Any]]:
    """One row per layer: its key, label and the count of the organisation's model elements, and the kinds in it.

    The counts come from count_layers: ``count`` is the ``model`` figure (the elements the map draws), ``total`` the
    figure on the architecture dashboard's tab and the Composer's layer card, and ``not_drawn`` the records the
    layer lists that the map does not draw, so drawn plus not drawn is the dashboard's figure on every layer."""
    from app.models.archimate_core import ArchiMateElement
    from app.services import archimate_viewpoint_service as counting
    from app.utils.tenant_sql import current_org_id

    org_id = current_org_id()
    rows = {row["layer"]: row for row in counting.count_layers(org_id)}

    # The kinds breakdown only: how many of each type sit in each layer. The counts are count_layers'.
    type_column = db.func.coalesce(ArchiMateElement.type, "Unspecified")
    stored = counting._stored_layer_column()
    kinds_query = (
        db.select(stored, type_column, db.func.count(ArchiMateElement.id))
        .group_by(stored, type_column)
        .where(ArchiMateElement.organization_id == org_id)
    )
    kinds_by_layer = {key: {} for key in LAYERS}
    for stored_layer, type_name, n in db.session.execute(kinds_query).all():
        layer = counting.element_layer(stored_layer, type_name)
        key = "other" if layer == counting.COMPOSITE_LAYER else layer
        if key in kinds_by_layer:
            kinds_by_layer[key][type_name] = kinds_by_layer[key].get(type_name, 0) + n

    out = []
    for key in LAYERS:
        row = rows[_counting_layer(key)]
        drawn = row["model"]
        total = row["count"]  # None when the register could not be counted: not known, never the drawn figure
        kinds = sorted(kinds_by_layer[key].items(), key=lambda kv: (-kv[1], kv[0]))
        out.append({
            "layer": key, "label": LAYER_LABELS[key], "count": drawn, "total": total, "not_drawn": None if total is None else total - drawn,
            "kinds": [{"type": t, "count": c} for t, c in kinds],
        })
    return out


def build_model_map(layer: Optional[str] = None) -> Dict[str, Any]:
    """The map for the signed-in organisation.

    ``mode`` is ``elements`` when every element is drawn, ``clusters`` when the model is over ``MAX_NODES`` and
    no layer was asked for (one node per layer), and ``elements`` again for a single layer.
    """
    from app.models.archimate_core import ArchiMateElement as E, ArchiMateRelationship as R

    counts = layer_counts()
    total = sum(c["count"] for c in counts)
    store_total = None if any(c["total"] is None for c in counts) else sum(c["total"] for c in counts)
    wanted = layer if layer in LAYERS else None
    base = {"counts": counts, "total": total, "store_total": store_total, "layer": wanted, "max_nodes": MAX_NODES}

    if wanted is None and total > MAX_NODES:
        nodes = [{"id": "layer:" + c["layer"], "name": c["label"], "layer": c["layer"], "count": c["count"],
                  "cluster": True} for c in counts if c["count"]]
        return {**base, "mode": "clusters", "nodes": nodes, "edges": [], "truncated": False, "shown": total}

    from app.services import archimate_viewpoint_service as counting
    from app.utils.tenant_sql import current_org_id

    query = counting.layer_model_query(_counting_layer(wanted), current_org_id()) if wanted else E.query
    query = query.with_entities(E.id, E.name, E.type, E.layer, E.source_kind, E.source_label, E.source_ref,
                                E.source_recorded_at, E.recorded_at).order_by(E.id)
    rows = query.limit(MAX_NODES + 1).all()
    truncated = len(rows) > MAX_NODES
    rows = rows[:MAX_NODES]

    nodes = []
    for r in rows:
        key = layer_key(r.layer, r.type)
        when = r.source_recorded_at or r.recorded_at
        nodes.append({
            "id": r.id, "name": r.name or "Element %d" % r.id, "type": r.type or "Element", "layer": key,
            "source": origin_sentence(r.source_kind, r.source_label, r.source_ref),
            "as_of": _day(footer._naive_utc(when)),
            "href": element_href(r.id, key, r.type),
        })
    ids = {n["id"] for n in nodes}
    edges = []
    unconnected = 0
    if ids:
        rels = db.session.execute(db.select(R.source_id, R.target_id, R.type)).all()
        edges = [{"source": s, "target": t, "type": ty} for s, t, ty in rels if s in ids and t in ids]
        # An element is connected when any relationship in the model touches it, whether or not the other end
        # is drawn (a single layer, or the first MAX_NODES), so the ring of unconnected elements is never
        # an artefact of what was left out of the picture.
        touched = {end for s, t, _ty in rels for end in (s, t)}
        for n in nodes:
            n["connected"] = n["id"] in touched
        unconnected = sum(1 for n in nodes if not n["connected"])
    return {**base, "mode": "elements", "nodes": nodes, "edges": edges, "truncated": truncated,
            "shown": len(nodes), "unconnected": unconnected}
