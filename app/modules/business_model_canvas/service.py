"""Service helpers for the Business Model Canvas module.

Kept deliberately thin — the model already carries most of the logic
(get_block/set_block/to_dict). This module exists for the small pieces of
business logic that don't belong on the ORM model itself.

``project_canvas`` (below) is the one projection read shared by both canvas
modules — ``business_case`` imports it rather than growing a second copy.
``_current_organization_id`` (below) is the same — ``business_case`` imports
it too, rather than growing a second copy of the same g-then-current_user
resolution the route layer already uses.
"""

from collections import defaultdict

from flask import g
from flask_login import current_user

from app import db
from app.models.business_model import (
    CANVAS_BLOCKS,
    OPERATING_MODEL_TYPES,
    BusinessModelCanvas,
)
from app.config.archimate_viewpoints import CANVAS_TEMPLATES
from app.modules.intelligence.services.derived_facts import (
    latest_derivation_run,
    list_derived_facts,
)
from app.modules.intelligence.services.latency_probe import record_query_latency
from app.modules.intelligence.services.reason_codes import validate_reason_code


def _current_organization_id():
    """The plain int this request belongs to, or None outside a tenant
    request (e.g. an anonymous caller) -- same source and reasoning as the
    route-level helpers in business_model_canvas/routes.py and
    business_case/routes.py: ``g.current_org_id`` is what the tenant-
    isolation listener keys off, read here rather than
    ``current_user.organization`` (an ORM relationship), with a fallback to
    ``current_user.organization_id`` for the rare caller that reaches this
    service outside a request that set ``g.current_org_id``. On
    AnonymousUser (no session), both reads come back None honestly -- never
    an AttributeError, and never another tenant's data."""
    org_id = getattr(g, "current_org_id", None)
    if org_id is not None:
        return int(org_id)
    org_id = getattr(current_user, "organization_id", None)
    return int(org_id) if org_id is not None else None


def list_canvases():
    """Return all canvases for the current tenant, most recently updated first."""
    return (
        BusinessModelCanvas.query.order_by(BusinessModelCanvas.updated_at.desc())
        .all()
    )


def get_canvas_or_none(canvas_id):
    # A primary-key .query.get(id) does not reliably carry the ORM tenant
    # listener's WHERE clause the way a filtered query does, so a guessed id
    # belonging to another organisation would still be found. Filter
    # explicitly, the same two-layer rule applied elsewhere in this codebase.
    #
    # current_user.organization_id was read directly here, which crashes
    # with AttributeError on AnonymousUser (no such attribute) -- reachable
    # from get_viewpoint_data's canvas_id lookup on an unauthenticated
    # request. _current_organization_id() returns None instead, and with no
    # organisation there is no tenant to filter on, so this returns None:
    # never another tenant's canvas, never a crash.
    org_id = _current_organization_id()
    if org_id is None:
        return None
    return BusinessModelCanvas.query.filter_by(
        id=canvas_id, organization_id=org_id
    ).first()


def create_canvas(name, description=None, operating_model_type=None):
    if operating_model_type and operating_model_type not in OPERATING_MODEL_TYPES:
        raise ValueError(f"Invalid operating_model_type: {operating_model_type}")
    canvas = BusinessModelCanvas(
        name=name or "Untitled Canvas",
        description=description,
        operating_model_type=operating_model_type,
    )
    db.session.add(canvas)
    db.session.commit()
    return canvas


def update_canvas_meta(canvas, name=None, description=None, operating_model_type=None):
    if name is not None:
        canvas.name = name
    if description is not None:
        canvas.description = description
    if operating_model_type is not None:
        if operating_model_type and operating_model_type not in OPERATING_MODEL_TYPES:
            raise ValueError(f"Invalid operating_model_type: {operating_model_type}")
        canvas.operating_model_type = operating_model_type or None
    db.session.commit()
    return canvas


def save_block(canvas, block_key, content):
    if block_key not in CANVAS_BLOCKS:
        raise ValueError(f"Unknown canvas block: {block_key}")
    canvas.set_block(block_key, content)
    db.session.commit()
    return canvas


def delete_canvas(canvas):
    db.session.delete(canvas)
    db.session.commit()


# ---------------------------------------------------------------------------
# Projection — pre-population and flags
# ---------------------------------------------------------------------------
#
# project_canvas() is the one read over elements, relationships, derived
# facts, risks and work packages that places profiled elements in their
# zones, computes the three flags from the existing readers, and returns
# every absence as a REASON_CODES member. Read-only: no db.session.add /
# .commit / .update is reachable from here (tests/test_canvas_projection.py
# asserts zero INSERT/UPDATE with a SQL-statement listener).
#
# Membership: an element belongs to a `type_profile` or `type_profile_anchor`
# zone when its type is one of the zone's `element_types` AND its
# `acm_properties.profile` equals the zone's `profile` — tenant-wide, not
# scoped to one saved diagram. `anchor_box` / `fill_relationship` are read
# for the flag computations below (an explicit `realization` into an entry,
# and the risk-to-entry link), not as an additional membership filter: a
# tenant with a Stakeholder profiled as a customer segment and a Value
# profiled as a value proposition sees the Value placed by type and profile
# alone, before any relationship between the two exists.

_CANVAS_BOX_EMPTY = validate_reason_code("canvas_box_empty")
_CANVAS_BOX_NOT_DERIVED = validate_reason_code("canvas_box_not_derived")
_REVENUE_INCOMPLETE = validate_reason_code("revenue_incomplete")
_COST_INCOMPLETE = validate_reason_code("cost_incomplete")
_PROFILE_NOT_SET = validate_reason_code("profile_not_set")
_NO_REALISING_ELEMENT = validate_reason_code("no_realising_element")
_DERIVATION_STALE = validate_reason_code("derivation_stale")
_DERIVATION_NOT_COMPUTED = validate_reason_code("derivation_not_computed")

# The ArchiMate 3.2 core behaviour element type names (business, application
# and technology process / function / interaction / event / service) plus
# Capability — the source types the `nothing_realises` flag's explicit
# realization check accepts, per the design's own wording ("a Capability or
# core behaviour element"). Private to this one flag: it names nothing about
# which relationships are legal to create (that authority stays
# ArchimateValidityService / VALID_RELATIONSHIPS, untouched here) — only
# which existing rows count toward this one read.
_REALISING_SOURCE_TYPES = frozenset({
    "Capability",
    "BusinessProcess", "BusinessFunction", "BusinessInteraction",
    "BusinessEvent", "BusinessService",
    "ApplicationProcess", "ApplicationFunction", "ApplicationInteraction",
    "ApplicationEvent", "ApplicationService",
    "TechnologyProcess", "TechnologyFunction", "TechnologyInteraction",
    "TechnologyEvent", "TechnologyService",
})

_CLASSIFYING_MEMBERSHIP = ("type_profile", "type_profile_anchor", "register")


def _acm_property_value(acm_properties, key):
    """Read one acm_properties value.

    Handles both the ``{"value": ..., "source": ...}`` shape
    ``PropertyService.merge_properties`` writes and a bare value — the same
    two-shape handling already applied inline in
    ``app/modules/architecture_assistant/property_service.py``
    (``is_visible``, ``calculate_element_score``), not a new pattern.
    """
    raw = (acm_properties or {}).get(key)
    if isinstance(raw, dict):
        return raw.get("value")
    return raw


def _profile_of(element):
    return _acm_property_value(element.acm_properties, "profile")


# ── Read seams ────────────────────────────────────────────────────────────
# Each isolated as its own function, mirroring
# app/modules/intelligence/services/derived_facts.py's own documented
# reason (`_apply_default_staleness_filter`): a mutation-proof test
# monkeypatches exactly one of these to drop its explicit predicate and
# confirm the matching cross-tenant test goes red, without editing the
# function under test.


def _read_zone_elements(organization_id, element_types):
    if not element_types:
        return []
    from app.models.archimate_core import ArchiMateElement

    return (
        db.session.execute(
            db.select(ArchiMateElement).where(
                ArchiMateElement.organization_id == organization_id,
                ArchiMateElement.type.in_(sorted(element_types)),
            )
        )
        .scalars()
        .all()
    )


def _read_elements_by_id(organization_id, element_ids):
    if not element_ids:
        return []
    from app.models.archimate_core import ArchiMateElement

    return (
        db.session.execute(
            db.select(ArchiMateElement).where(
                ArchiMateElement.organization_id == organization_id,
                ArchiMateElement.id.in_(sorted(element_ids)),
            )
        )
        .scalars()
        .all()
    )


def _read_zone_relationships(organization_id, rel_types):
    if not rel_types:
        return []
    from app.models.archimate_core import ArchiMateRelationship

    return (
        db.session.execute(
            db.select(ArchiMateRelationship).where(
                ArchiMateRelationship.organization_id == organization_id,
                ArchiMateRelationship.type.in_(sorted(rel_types)),
            )
        )
        .scalars()
        .all()
    )


def _read_relationships_by_source_ids(organization_id, source_ids):
    """Relationships whose source is one of *source_ids*, tenant-scoped —
    the same explicit-predicate pattern as every other read seam here, so a
    mutation-proof test can monkeypatch exactly this to drop the predicate
    and confirm the matching cross-tenant test goes red."""
    if not source_ids:
        return []
    from app.models.archimate_core import ArchiMateRelationship

    return (
        db.session.execute(
            db.select(ArchiMateRelationship).where(
                ArchiMateRelationship.organization_id == organization_id,
                ArchiMateRelationship.source_id.in_(sorted(source_ids)),
            )
        )
        .scalars()
        .all()
    )


def _read_canvas_risks(organization_id):
    from app.models.risk import Risk

    return (
        db.session.execute(
            db.select(Risk).where(
                Risk.organization_id == organization_id,
                Risk.archimate_element_id.isnot(None),
            )
        )
        .scalars()
        .all()
    )


def _read_work_packages_by_element(element_ids):
    """``UnifiedWorkPackage`` carries no ``organization_id`` of its own
    (documented in query_service.py's ``programme_for_element``) — tenant
    safety here is inherited entirely from *element_ids* already having
    come from a tenant-scoped read; this function never queries
    independently of that set. Isolated as its own seam so a mutation-proof
    test can feed it a foreign id and show a foreign work package leaking
    when the UPSTREAM element scoping is what is disabled."""
    if not element_ids:
        return []
    from app.models.unified_work_package import UnifiedWorkPackage

    return (
        db.session.execute(
            db.select(UnifiedWorkPackage).where(
                UnifiedWorkPackage.archimate_element_id.in_(sorted(element_ids))
            )
        )
        .scalars()
        .all()
    )


def project_canvas(template_key, record, *, organization_id):
    """The one projection read: places profiled elements in their zones,
    computes ``high_risk`` / ``nothing_realises`` / ``stale`` from the
    existing readers, and returns every absence as a reason code. Read-only.
    """
    from app.services.archimate_viewpoint_service import _normalize_rel_type

    template = CANVAS_TEMPLATES[template_key]
    zones = template["zones"]

    with record_query_latency("canvas_projection") as scope:
        scope.organization_id = organization_id

        element_types = {t for z in zones for t in z["element_types"]}
        elements_by_id = {}
        elements_by_type = defaultdict(list)
        for el in _read_zone_elements(organization_id, element_types):
            elements_by_id[el.id] = el
            elements_by_type[el.type].append(el)
        scope.explicit_rows = len(elements_by_id)

        rel_types = {z["fill_relationship"]["type"] for z in zones if z.get("fill_relationship")}
        rel_types.add("realization")
        relationships = _read_zone_relationships(organization_id, rel_types) if elements_by_id else []

        realization_into = defaultdict(list)
        realization_source_ids = set()
        for rel in relationships:
            if _normalize_rel_type(rel.type) == "realization":
                realization_into[rel.target_id].append(rel.source_id)
                realization_source_ids.add(rel.source_id)

        missing_source_ids = realization_source_ids - set(elements_by_id)
        for el in _read_elements_by_id(organization_id, missing_source_ids):
            elements_by_id[el.id] = el

        derivation_run = latest_derivation_run(organization_id)
        derived_by_target = defaultdict(list)
        for row in list_derived_facts(organization_id, include_stale=True):
            if row["derived_type"] == "Realization":
                derived_by_target[row["target_element_id"]].append(row)

        risk_id_by_element = {}
        high_risk_elements = set()
        blast_seeds = []
        for risk in _read_canvas_risks(organization_id):
            risk_id_by_element[risk.archimate_element_id] = risk.id
            if risk.risk_level not in ("high", "critical"):
                continue
            high_risk_elements.add(risk.archimate_element_id)
            blast_seeds.append(risk.archimate_element_id)
        high_risk_elements |= _risk_blast_targets(blast_seeds, organization_id)

        # Two passes: attribute/composed zones read OTHER zones' entries
        # (revenue_streams reads value_propositions' entries; cost_structure
        # reads whichever of key_resources/key_activities/unfair_advantage
        # carry a Resource or Capability entry) and CANVAS_TEMPLATES' own
        # zone order does not always put a reader after everything it reads
        # (Lean's cost_structure is zone 7, unfair_advantage is zone 9) — so
        # every element-membership zone is placed first, then every
        # attribute/composed zone reads the complete result, never a
        # partial one depending on where it sits in the template's order.
        zones_out = [None] * len(zones)
        placed_ids = set()
        entries_by_box_key = {}
        for index, zone in enumerate(zones):
            if zone["membership"] in _CLASSIFYING_MEMBERSHIP:
                zone_out = _project_element_zone(
                    zone, record, elements_by_id, elements_by_type,
                    realization_into, derived_by_target, derivation_run,
                    high_risk_elements, risk_id_by_element, placed_ids,
                )
                zones_out[index] = zone_out
                entries_by_box_key[zone["box_key"]] = zone_out["entries"]

        for index, zone in enumerate(zones):
            if zone["membership"] == "composed":
                note = getattr(record, zone["box_key"], None) if hasattr(record, zone["box_key"]) else None
                zones_out[index] = {
                    "box_key": zone["box_key"], "label": zone["label"], "entries": [],
                    "note": note, "attribute_items": [],
                    "reasons": [zone["empty_reason"]],
                }
            elif zone["membership"] == "attribute":
                note = getattr(record, zone["box_key"], None) if hasattr(record, zone["box_key"]) else None
                zones_out[index] = _project_attribute_zone(
                    zone, note, elements_by_id, entries_by_box_key,
                )

        _attach_work_package_ids(zones_out, elements_by_id)

        classifying_types = {
            t for z in zones if z["membership"] in _CLASSIFYING_MEMBERSHIP
            for t in z["element_types"]
        }
        unclassified = []
        seen = set()
        for el_type in classifying_types:
            for el in elements_by_type.get(el_type, []):
                if el.id in placed_ids or el.id in seen:
                    continue
                if _profile_of(el):
                    continue
                seen.add(el.id)
                unclassified.append({
                    "element_id": el.id, "name": el.name, "type": el.type,
                    "reason": _PROFILE_NOT_SET,
                })

    return {
        "template_key": template_key,
        "record_id": getattr(record, "id", None),
        "saved_diagram_id": getattr(record, "saved_diagram_id", None),
        "zones": zones_out,
        "unclassified": unclassified,
        "derivation_state": "current" if derivation_run is not None else "not_computed",
        "latency_ms": scope.latency_ms,
    }


def _risk_blast_targets(risk_element_ids, organization_id):
    """The elements an explicit relationship reaches, one hop out from every
    high-risk element — batched into a single relationship read over all the
    ids at once, instead of calling ``risk_for_element`` per id.

    *organization_id* is the caller's already-resolved tenant, passed down
    explicitly rather than left for ``risk_for_element`` to re-read from
    ``g`` — this read can run inside a projection that itself was handed an
    explicit organisation_id, and a second, independent ``g`` read has no
    reason to agree with it in every calling context."""
    if not risk_element_ids:
        return set()

    targets = set()
    for rel in _read_relationships_by_source_ids(organization_id, risk_element_ids):
        targets.add(rel.target_id)
    return targets


def _project_element_zone(zone, record, elements_by_id, elements_by_type,
                           realization_into, derived_by_target, derivation_run,
                           high_risk_elements, risk_id_by_element, placed_ids):
    """A ``type_profile`` / ``type_profile_anchor`` / ``register`` zone —
    every entry is a real element (all three flags apply)."""
    box_key = zone["box_key"]
    note = getattr(record, box_key, None) if hasattr(record, box_key) else None

    candidates = [
        el for el_type in zone["element_types"]
        for el in elements_by_type.get(el_type, [])
        if _profile_of(el) == zone["profile"]
    ]
    entries = []
    for el in sorted(candidates, key=lambda e: e.id):
        placed_ids.add(el.id)
        entries.append(_project_entry(
            el, elements_by_id, realization_into, derived_by_target,
            derivation_run, high_risk_elements, risk_id_by_element,
        ))

    return {
        "box_key": box_key, "label": zone["label"], "entries": entries,
        "note": note, "attribute_items": [],
        "reasons": [] if entries else [zone["empty_reason"]],
    }


def _project_entry(el, elements_by_id, realization_into, derived_by_target,
                    derivation_run, high_risk_elements, risk_id_by_element):
    reasons = []
    explicit_realizes = any(
        elements_by_id[src].type in _REALISING_SOURCE_TYPES
        for src in realization_into.get(el.id, [])
        if src in elements_by_id
    )
    derived_here = derived_by_target.get(el.id, [])
    non_stale_derived = [r for r in derived_here if not r["stale"]]
    stale_derived = [r for r in derived_here if r["stale"]]

    if derivation_run is None:
        nothing_realises = None
        if not explicit_realizes:
            reasons.append(_DERIVATION_NOT_COMPUTED)
    else:
        nothing_realises = not (explicit_realizes or non_stale_derived)
        if nothing_realises:
            reasons.append(_NO_REALISING_ELEMENT)

    stale = bool(stale_derived) and not explicit_realizes and not non_stale_derived
    if stale:
        reasons.append(_DERIVATION_STALE)

    if not explicit_realizes and non_stale_derived:
        truth_class = "derived_intelligence"
        derived_id = non_stale_derived[0]["id"]
    else:
        truth_class = "authoritative_fact"
        derived_id = None

    entry = {
        "element_id": el.id,
        "name": el.name,
        "type": el.type,
        "profile": _profile_of(el),
        "flags": {
            "high_risk": el.id in high_risk_elements,
            "nothing_realises": nothing_realises,
            "stale": stale,
        },
        "reasons": reasons,
        "truth_class": truth_class,
    }
    if derived_id is not None:
        entry["derived_id"] = derived_id
    risk_id = risk_id_by_element.get(el.id)
    if risk_id is not None:
        entry["risk_id"] = risk_id
    return entry


def _project_attribute_zone(zone, note, elements_by_id, entries_by_box_key):
    box_key = zone["box_key"]
    attribute_keys = zone["attribute_keys"]

    if zone.get("anchor_box"):
        source_ids = [e["element_id"] for e in entries_by_box_key.get(zone["anchor_box"], [])]
    else:
        # No single anchor (e.g. Cost Structure over Resource/Capability):
        # attaches to the entries of every element-membership zone whose
        # element type is one of this zone's element_types — every such
        # zone has already been placed (project_canvas's first pass), so
        # this is complete regardless of where THIS zone sits in the
        # template's own order.
        source_ids = [
            e["element_id"]
            for entries in entries_by_box_key.values()
            for e in entries
            if elements_by_id.get(e["element_id"]) is not None
            and elements_by_id[e["element_id"]].type in zone["element_types"]
        ]

    items = []
    for element_id in source_ids:
        el = elements_by_id.get(element_id)
        if el is None:
            continue
        values = {key: _acm_property_value(el.acm_properties, key) for key in attribute_keys}
        items.append({"element_id": el.id, "name": el.name, **values})

    total = None
    reasons = []
    if not items:
        reasons = [zone["empty_reason"]]
    else:
        amount_key = next((k for k in attribute_keys if "amount" in k), None)
        currency_key = "currency" if "currency" in attribute_keys else None
        if amount_key and currency_key:
            amounts = [it.get(amount_key) for it in items]
            currencies = {it.get(currency_key) for it in items}
            if all(a not in (None, "") for a in amounts) and len(currencies) == 1:
                try:
                    total = {
                        "value": sum(float(a) for a in amounts),
                        "currency": next(iter(currencies)),
                        "label": "sum of entered amounts",
                    }
                except (TypeError, ValueError):
                    total = None
        if total is None:
            reasons = [_REVENUE_INCOMPLETE if box_key.startswith("revenue") else _COST_INCOMPLETE]

    zone_out = {
        "box_key": box_key, "label": zone["label"], "entries": [],
        "note": note, "attribute_items": items, "reasons": reasons,
    }
    if total is not None:
        zone_out["total"] = total
    return zone_out


def _attach_work_package_ids(zones_out, elements_by_id):
    """``work_package_id`` on any entry whose element is a WorkPackage — the
    unified work package reached through its element link, the same shape
    as ``risk_id``; pulling its dates and other fields onto the payload is a
    separate, later piece of work."""
    wp_entry_by_element = {}
    for zone in zones_out:
        for entry in zone["entries"]:
            el = elements_by_id.get(entry["element_id"])
            if el is not None and el.type == "WorkPackage":
                wp_entry_by_element[entry["element_id"]] = entry

    for wp in _read_work_packages_by_element(list(wp_entry_by_element)):
        entry = wp_entry_by_element.get(wp.archimate_element_id)
        if entry is not None:
            entry["work_package_id"] = wp.id
