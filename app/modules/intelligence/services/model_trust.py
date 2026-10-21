"""T-ain-04: the one resolver for how complete and how fresh each area of the model is.

An *area* is an ArchiMate layer (``application``) or one element type
(``ApplicationComponent``). For each area the resolver reports, as measured
fractions with their denominators:

    coverage   owner, lifecycle, cost, relationship -- how many of the elements
               that can carry the fact do carry it ("31 of 40 owned")
    freshness  the newest and the median last change of the area's elements

It is not a health score: nothing is weighted, combined or graded. An area with
no elements reads "nothing recorded", never 0 percent, and a fact an element
type does not carry (a Goal has no cost) is left out of that area's
denominator instead of counting against it.

Where each figure is read from (nothing here keeps a copy):

    owner         ``query_service._resolve_application_owners_batch`` -- the
                  owner readers behind the impact answer's owner column
    lifecycle     ``ApplicationComponent.lifecycle_status``
    cost          ``application_cost_accessor.has_recorded_cost``
    relationship  ``ArchiMateRelationship`` source or target
    last change   the latest ``event_log`` event for the element, or the
                  element's own ``recorded_at`` stamp, whichever is newer

The Catalogue index, the answer envelope and the assistant connection's
``get_model_trust`` tool all call ``model_trust`` and show what it returns.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Dict, Iterable, List, Optional

from app.extensions import db

LAYERS = (
    "strategy",
    "business",
    "application",
    "technology",
    "physical",
    "motivation",
    "implementation",
)
OTHER_LAYER = "other"

_LAYER_ALIASES = {"implementation_migration": "implementation", "implementation & migration": "implementation"}

# The element types whose owner, lifecycle or cost the product stores. The owner
# readers resolve exactly these two; lifecycle and cost live on the application
# record. Every other type simply does not carry the fact.
OWNER_TYPES = frozenset({"ApplicationComponent", "Capability"})
LIFECYCLE_TYPES = frozenset({"ApplicationComponent"})
COST_TYPES = frozenset({"ApplicationComponent"})

MEASURES = ("owner", "lifecycle", "cost", "relationship")

BASIS = (
    "Counted from this organisation's recorded elements, relationships, owners and change history. "
    "Each figure is a plain count over the elements that can carry the fact."
)

_ENTITY_TYPE = "archimate_element"


class UnknownArea(ValueError):
    """The area is neither a layer nor an element type."""


def normalise_layer(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    text = _LAYER_ALIASES.get(text, text)
    return text if text in LAYERS else OTHER_LAYER


def _naive_utc(value: Optional[_dt.datetime]) -> Optional[_dt.datetime]:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    return value


def _humanise_type(type_name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", type_name).lower()


def _ago(days: int) -> str:
    if days <= 0:
        return "today"
    if days == 1:
        return "1 day ago"
    if days < 60:
        return f"{days} days ago"
    if days < 730:
        return f"{days // 30} months ago"
    return f"{days // 365} years ago"


def _median(values: List[_dt.datetime]) -> _dt.datetime:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return ordered[mid - 1] + (ordered[mid] - ordered[mid - 1]) / 2


def known_type_names() -> List[str]:
    from app.models.archimate_element_types import ALL_ELEMENT_TYPES

    return list(ALL_ELEMENT_TYPES)


def resolve_area(area: str, present_types: Iterable[str] = ()) -> Dict[str, str]:
    """``{"kind", "key"}`` for a layer or type name, or ``UnknownArea``."""
    text = str(area or "").strip()
    lowered = text.lower()
    if lowered in LAYERS or lowered == OTHER_LAYER or lowered in _LAYER_ALIASES:
        return {"kind": "layer", "key": normalise_layer(lowered)}
    candidates = list(present_types) + known_type_names()
    for name in candidates:
        if name == text:
            return {"kind": "type", "key": name}
    for name in candidates:
        if name.lower() == lowered:
            return {"kind": "type", "key": name}
    raise UnknownArea(text)


# --------------------------------------------------------------------------- #
# Reading the organisation's model                                            #
# --------------------------------------------------------------------------- #


def _read_elements(organization_id: int) -> List[Dict[str, Any]]:
    from app.models import ArchiMateElement

    rows = db.session.execute(
        db.select(
            ArchiMateElement.id,
            ArchiMateElement.type,
            ArchiMateElement.layer,
            ArchiMateElement.recorded_at,
        ).where(ArchiMateElement.organization_id == organization_id)
    ).all()
    return [
        {
            "id": row.id,
            "type": row.type or "",
            "layer": normalise_layer(row.layer),
            "recorded_at": _naive_utc(row.recorded_at),
        }
        for row in rows
    ]


def _connected_ids(organization_id: int) -> set:
    from app.models import ArchiMateRelationship

    connected: set = set()
    for column in (ArchiMateRelationship.source_id, ArchiMateRelationship.target_id):
        connected.update(
            db.session.execute(
                db.select(column)
                .where(ArchiMateRelationship.organization_id == organization_id)
                .where(column.is_not(None))
                .distinct()
            ).scalars()
        )
    return connected


def _latest_events(organization_id: int) -> Dict[int, _dt.datetime]:
    from app.models.event_log import EventLogRecord

    rows = db.session.execute(
        db.select(EventLogRecord.entity_id, db.func.max(EventLogRecord.created_at))
        .where(EventLogRecord.organization_id == organization_id)
        .where(EventLogRecord.entity_type == _ENTITY_TYPE)
        .where(EventLogRecord.entity_id.is_not(None))
        .group_by(EventLogRecord.entity_id)
    ).all()
    return {entity_id: _naive_utc(created) for entity_id, created in rows}


def _owned_ids(organization_id: int, elements: List[Dict[str, Any]]) -> set:
    from app.modules.intelligence.services.query_service import _resolve_application_owners_batch

    carriers = [e for e in elements if e["type"] in OWNER_TYPES]
    if not carriers:
        return set()
    identity = {str(e["id"]): {"type": e["type"]} for e in carriers}
    owners = _resolve_application_owners_batch([e["id"] for e in carriers], organization_id, identity)
    return {element_id for element_id, found in owners.items() if found}


def _application_facts(organization_id: int, elements: List[Dict[str, Any]]):
    """``(ids with a lifecycle, ids with a recorded cost)``."""
    from app.models.application_portfolio import ApplicationComponent
    from app.services.application_cost_accessor import has_recorded_cost

    ids = [e["id"] for e in elements if e["type"] in LIFECYCLE_TYPES or e["type"] in COST_TYPES]
    with_lifecycle: set = set()
    with_cost: set = set()
    if not ids:
        return with_lifecycle, with_cost
    components = db.session.execute(
        db.select(ApplicationComponent)
        .where(ApplicationComponent.archimate_element_id.in_(ids))
        .where(ApplicationComponent.organization_id == organization_id)
        .order_by(ApplicationComponent.id)
    ).scalars()
    for component in components:
        element_id = component.archimate_element_id
        if (component.lifecycle_status or "").strip():
            with_lifecycle.add(element_id)
        if has_recorded_cost(component):
            with_cost.add(element_id)
    return with_lifecycle, with_cost


# --------------------------------------------------------------------------- #
# Building an area                                                            #
# --------------------------------------------------------------------------- #


def _area_label(kind: str, key: str) -> str:
    if kind == "layer":
        return "elements in no layer" if key == OTHER_LAYER else f"{key} layer"
    return f"{_humanise_type(key)} elements"


def _measure(have: int, carriers: int, total: int, phrase: str, carrier_phrase: str) -> Optional[Dict[str, Any]]:
    if carriers == 0:
        return None
    entry: Dict[str, Any] = {"have": have, "of": carriers, "text": f"{have} of {carriers} {phrase}"}
    if carriers != total:
        entry["scope"] = f"counted over the {carriers} of {total} elements that {carrier_phrase}"
    return entry


def _freshness(dates: List[_dt.datetime], total: int, now: _dt.datetime) -> Dict[str, Any]:
    if not dates:
        return {
            "dated": 0,
            "of": total,
            "newest": None,
            "median": None,
            "newest_days_ago": None,
            "median_days_ago": None,
            "median_text": None,
            "text": "last change not recorded",
        }
    newest = max(dates)
    median = _median(dates)
    newest_days = max(0, int((now - newest).total_seconds() // 86400))
    median_days = max(0, int((now - median).total_seconds() // 86400))
    return {
        "dated": len(dates),
        "of": total,
        "newest": newest.isoformat() + "Z",
        "median": median.isoformat() + "Z",
        "newest_days_ago": newest_days,
        "median_days_ago": median_days,
        "median_text": f"median {_ago(median_days)}",
        "text": f"last change {_ago(newest_days)}",
    }


def _headline(coverage: Dict[str, Any], total: int) -> str:
    for name in ("owner", "relationship"):
        entry = coverage.get(name)
        if entry:
            if entry.get("scope"):
                return f"{entry['text']} (the {entry['of']} of {total} elements that can have an owner)"
            return entry["text"]
    return ""


def _build_area(kind: str, key: str, members: List[Dict[str, Any]], facts: Dict[str, set], now: _dt.datetime):
    label = _area_label(kind, key)
    base = {"area": key, "kind": kind, "label": label}
    if kind == "layer":
        base["layer"] = key
    if not members:
        return {
            **base,
            "state": "nothing_recorded",
            "elements_total": 0,
            "coverage": None,
            "freshness": None,
            "line": f"From the {label}: nothing recorded",
        }
    total = len(members)
    ids = [m["id"] for m in members]

    def count(measure_types, source):
        carriers = [m for m in members if measure_types is None or m["type"] in measure_types]
        return sum(1 for m in carriers if m["id"] in source), len(carriers)

    have_owner, owner_of = count(OWNER_TYPES, facts["owned"])
    have_life, life_of = count(LIFECYCLE_TYPES, facts["lifecycle"])
    have_cost, cost_of = count(COST_TYPES, facts["cost"])
    have_rel, rel_of = count(None, facts["connected"])
    coverage = {
        "owner": _measure(have_owner, owner_of, total, "owned", "carry an owner"),
        "lifecycle": _measure(have_life, life_of, total, "have a lifecycle", "carry a lifecycle"),
        "cost": _measure(have_cost, cost_of, total, "have a cost", "carry a cost"),
        "relationship": _measure(have_rel, rel_of, total, "are connected", "can be connected"),
    }
    dates = [facts["changed"][i] for i in ids if i in facts["changed"]]
    freshness = _freshness(dates, total, now)
    headline = _headline(coverage, total)
    return {
        **base,
        "state": "recorded",
        "elements_total": total,
        "coverage": coverage,
        "freshness": freshness,
        "line": f"From the {label}: {headline}, {freshness['text']}",
    }


def _last_changes(elements: List[Dict[str, Any]], events: Dict[int, _dt.datetime]) -> Dict[int, _dt.datetime]:
    changed: Dict[int, _dt.datetime] = {}
    for element in elements:
        candidates = [d for d in (element["recorded_at"], events.get(element["id"])) if d is not None]
        if candidates:
            changed[element["id"]] = max(candidates)
    return changed


def model_trust(
    organization_id: Optional[int],
    area: Optional[str] = None,
    *,
    now: Optional[_dt.datetime] = None,
) -> Dict[str, Any]:
    """Coverage and freshness for one organisation.

    With no *area* every layer (empty ones included, reading "nothing
    recorded") and every element type present is returned; with an *area* (a
    layer or a type name) only that area is. ``UnknownArea`` for anything else.
    An organisation of ``None`` has no model and yields no areas.
    """
    now = _naive_utc(now) or _dt.datetime.utcnow()
    result: Dict[str, Any] = {
        "organization_id": organization_id,
        "computed_at": now.isoformat() + "Z",
        "basis": BASIS,
        "areas": [],
    }
    if organization_id is None:
        return result

    elements = _read_elements(organization_id)
    present_types = sorted({e["type"] for e in elements if e["type"]})
    wanted = resolve_area(area, present_types) if area else None

    if wanted is not None:
        if wanted["kind"] == "layer":
            elements = [e for e in elements if e["layer"] == wanted["key"]]
        else:
            elements = [e for e in elements if e["type"] == wanted["key"]]

    owned = _owned_ids(organization_id, elements)
    with_lifecycle, with_cost = _application_facts(organization_id, elements)
    facts = {
        "owned": owned,
        "lifecycle": with_lifecycle,
        "cost": with_cost,
        "connected": _connected_ids(organization_id) if elements else set(),
        "changed": _last_changes(elements, _latest_events(organization_id) if elements else {}),
    }

    if wanted is not None:
        result["areas"] = [_build_area(wanted["kind"], wanted["key"], elements, facts, now)]
        return result

    by_layer: Dict[str, List[Dict[str, Any]]] = {}
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for element in elements:
        by_layer.setdefault(element["layer"], []).append(element)
        if element["type"]:
            by_type.setdefault(element["type"], []).append(element)

    layer_keys = list(LAYERS) + ([OTHER_LAYER] if OTHER_LAYER in by_layer else [])
    for key in layer_keys:
        result["areas"].append(_build_area("layer", key, by_layer.get(key, []), facts, now))
    layer_order = {key: i for i, key in enumerate(layer_keys)}
    for type_name in sorted(
        by_type, key=lambda t: (layer_order.get(by_type[t][0]["layer"], 99), t.lower())
    ):
        result["areas"].append(_build_area("type", type_name, by_type[type_name], facts, now))
    return result


# --------------------------------------------------------------------------- #
# The answer envelope's share                                                 #
# --------------------------------------------------------------------------- #


def layers_of(organization_id: Optional[int], element_ids: Iterable[int]) -> List[str]:
    """The layers (in layer order) the named elements sit in, inside the organisation."""
    ids = sorted({int(i) for i in element_ids if isinstance(i, int) and not isinstance(i, bool)})
    if organization_id is None or not ids:
        return []
    from app.models import ArchiMateElement

    found = {
        normalise_layer(layer)
        for layer in db.session.execute(
            db.select(ArchiMateElement.layer)
            .where(ArchiMateElement.organization_id == organization_id)
            .where(ArchiMateElement.id.in_(ids))
        ).scalars()
    }
    order = list(LAYERS) + [OTHER_LAYER]
    return [layer for layer in order if layer in found]


def trust_for_answer(
    organization_id: Optional[int], element_ids: Iterable[int], *, now: Optional[_dt.datetime] = None
) -> List[Dict[str, Any]]:
    """The area records for the layers an answer's elements are drawn from."""
    areas: List[Dict[str, Any]] = []
    for layer in layers_of(organization_id, element_ids):
        areas.extend(model_trust(organization_id, layer, now=now)["areas"])
    return areas
