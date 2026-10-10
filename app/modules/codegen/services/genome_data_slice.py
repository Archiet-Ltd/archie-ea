"""
Enterprise Genome — DATA domain slice builder.

The second genome slice (after the APPLICATION reference), proving the
deterministic, 0-LLM, provenance-anchored pattern of
``app/modules/codegen/services/genome_to_bundle.py`` generalises beyond one
domain.

What it reads (for ONE organization):
  - ``data_object`` (application layer) and ``business_object`` / ``representation``
    / ``contract`` / ``product`` (business layer) ArchiMate elements — the modelled
    information objects that a GDPR Article 30 Record of Processing Activities is
    built around.
  - their ``access`` relationships (read / write / readwrite) to the behaviour /
    active-structure elements that touch them, and the applications those resolve to.

What it emits: a ``data`` genome slice — one *processing activity* node per
information object, each carrying **structural provenance** (the source
ArchiMate element id + type) that is **non-optional**. A node the builder cannot
anchor to a real element id is a build error (:class:`ProvenanceError`), never a
fabricated row. This is the traceability the product is sold on:
``processing activity → data element → its access edges → the systems``.

Determinism: no LLM, no clock, no randomness. Every collection is ordered by a
stable key (element id) and the whole slice is fingerprinted with a
``spec_hash`` over a canonical JSON serialisation, so two builds of an unchanged
model are byte-identical.
"""
from __future__ import annotations

import hashlib
import json
import logging

logger = logging.getLogger(__name__)

GENOME_VERSION = "2.0.0"

# The information-object element types the DATA slice projects. ArchiMate 3.2 has
# no distinct data layer — data_object is application-layer, the rest are
# business-layer — so the slice is a *logical* projection, per 02_schema.md §2.3.
#
# Entelim stores element `type` inconsistently: both snake_case ("data_object")
# and ArchiMate CamelCase ("DataObject") occur in the same table. We normalise
# to snake_case for matching so both are caught.
DATA_OBJECT_TYPES = frozenset(
    {"data_object", "business_object", "representation", "contract", "product"}
)

# Element type -> ArchiMate layer, for the provenance record's `layer` field.
_TYPE_LAYER = {
    "data_object": "application",
    "business_object": "business",
    "representation": "business",
    "contract": "business",
    "product": "business",
}


class ProvenanceError(ValueError):
    """A processing-activity node could not be anchored to a real source element.

    Raised rather than emitting a provenance-free row — the DATA slice's central
    invariant is that every Article 30 row traces to a modelled element id.
    """


def _norm_type(raw: str | None) -> str:
    """Normalise an element type to snake_case (``DataObject`` -> ``data_object``)."""
    if not raw:
        return ""
    s = raw.strip()
    # Insert underscores at camel boundaries, then lower-case.
    out = []
    for i, ch in enumerate(s):
        if ch.isupper() and i > 0 and (s[i - 1].islower() or s[i - 1].isdigit()):
            out.append("_")
        out.append(ch)
    return "".join(out).lower()


def _read_props(elem) -> dict:
    """Return an element's custom properties as a dict, tolerant of shape.

    Elements carry governance metadata in either ``custom_properties`` (JSON
    column) or ``properties`` (JSON-encoded text). Both are optional; a missing
    value yields ``{}`` so the caller renders an em dash, never a guess.
    """
    props = {}
    raw = getattr(elem, "custom_properties", None)
    if isinstance(raw, dict):
        props.update(raw)
    text = getattr(elem, "properties", None)
    if isinstance(text, str) and text.strip():
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                for k, v in parsed.items():
                    props.setdefault(k, v)
        except (ValueError, TypeError):
            pass
    return props


def _first(props: dict, *keys):
    """First non-empty value among ``keys`` in ``props``, else ``None``."""
    for k in keys:
        v = props.get(k)
        if v not in (None, "", []):
            return v
    return None


def build_data_genome_slice(organization_id: int, session=None) -> dict:
    """Build the DATA genome slice for one organization.

    Args:
        organization_id: tenant owner — REQUIRED. The builder scopes every query
            explicitly to this org (it runs outside a guaranteed request context,
            where the ORM tenant filter would not apply — see CLAUDE.md).
        session: optional SQLAlchemy session; defaults to ``db.session``.

    Returns:
        A ``data`` slice dict with a ``processing_activities`` list and a
        ``spec_hash``. Deterministic for a fixed model state.

    Raises:
        ProvenanceError: if any emitted activity lacks a source element id.
        ValueError: if ``organization_id`` is missing.
    """
    if organization_id is None:
        raise ValueError("organization_id is required — a genome cannot be un-scoped")

    from app import db
    from app.models.models import ArchiMateElement, ArchiMateRelationship

    sess = session or db.session

    # --- 1. Information objects, ordered by id for determinism -----------------
    elements = (
        sess.query(ArchiMateElement)
        .filter(ArchiMateElement.organization_id == organization_id)
        .order_by(ArchiMateElement.id.asc())
        .all()
    )
    objects = [e for e in elements if _norm_type(e.type) in DATA_OBJECT_TYPES]
    # id -> element, for resolving access-edge endpoints to real names.
    by_id = {e.id: e for e in elements}
    object_ids = {e.id for e in objects}

    # --- 2. Access edges touching those objects --------------------------------
    # An ArchiMate access relationship links a behaviour/active-structure element
    # to a data object; either endpoint may be the object depending on how it was
    # drawn, so we accept the object on either side and take the *other* end as
    # the system that processes it.
    access_edges = (
        sess.query(ArchiMateRelationship)
        .filter(ArchiMateRelationship.organization_id == organization_id)
        .filter(db.func.lower(ArchiMateRelationship.type) == "access")
        .order_by(ArchiMateRelationship.id.asc())
        .all()
    )

    systems_by_object: dict[int, list] = {oid: [] for oid in object_ids}
    system_element_ids: set[int] = set()
    for rel in access_edges:
        if rel.target_id in object_ids:
            obj_id, sys_id = rel.target_id, rel.source_id
        elif rel.source_id in object_ids:
            obj_id, sys_id = rel.source_id, rel.target_id
        else:
            continue
        sys_elem = by_id.get(sys_id)
        if sys_elem is None:
            # Endpoint outside this org / deleted — skip rather than invent a name.
            continue
        systems_by_object[obj_id].append(
            {
                "archimate_element_id": sys_elem.id,
                "name": sys_elem.name,
                "archimate_type": _norm_type(sys_elem.type),
                "access_mode": (rel.access_mode or "unspecified"),
            }
        )
        system_element_ids.add(sys_elem.id)

    # --- 2b. Suppliers linked to each system (ApplicationComponent) ------------
    # Read VendorContract and primary_vendor_product for each system that is an
    # ApplicationComponent, following the same pattern as GDPRService._processors_for.
    suppliers_by_system: dict[int, list] = {sid: [] for sid in system_element_ids}
    if system_element_ids:
        from app.models.application_portfolio import ApplicationComponent, VendorContract

        components = (
            sess.query(ApplicationComponent)
            .filter(
                ApplicationComponent.organization_id == organization_id,
                ApplicationComponent.archimate_element_id.in_(sorted(system_element_ids)),
            )
            .all()
        )
        component_by_eid = {c.archimate_element_id: c for c in components}
        # Reverse mapping: ApplicationComponent.id -> archimate_element_id
        eid_by_component_id = {c.id: c.archimate_element_id for c in components}
        if component_by_eid:
            contracts = (
                sess.query(VendorContract)
                .filter(
                    VendorContract.organization_id == organization_id,
                    VendorContract.application_id.in_(list(eid_by_component_id)),
                )
                .order_by(VendorContract.id)
                .all()
            )
            for contract in contracts:
                vendor = contract.vendor
                if vendor is None:
                    continue
                eid = eid_by_component_id.get(contract.application_id)
                if eid is not None:
                    suppliers_by_system[eid].append({
                        "vendor_id": vendor.id,
                        "name": vendor.display_name or vendor.name,
                        "link": "Contract: %s" % (contract.contract_name or "—"),
                    })
            for eid, component in component_by_eid.items():
                product = component.primary_vendor_product
                vendor = getattr(product, "vendor_organization", None) if product is not None else None
                if vendor is not None:
                    suppliers_by_system[eid].append({
                        "vendor_id": vendor.id,
                        "name": vendor.display_name or vendor.name,
                        "link": "Primary vendor product: %s" % (product.name or "—"),
                    })
                if (component.vendor_name or "").strip():
                    suppliers_by_system[eid].append({
                        "vendor_id": None,
                        "name": component.vendor_name.strip(),
                        "link": "Vendor named on the application record",
                    })

    # --- 2c. Flow relationships touching those objects -------------------------
    # ArchiMate flow relationships that link to data objects, showing data flows
    # between systems and the processing activity.
    flow_edges = (
        sess.query(ArchiMateRelationship)
        .filter(ArchiMateRelationship.organization_id == organization_id)
        .filter(db.func.lower(ArchiMateRelationship.type) == "flow")
        .order_by(ArchiMateRelationship.id.asc())
        .all()
    )
    flows_by_object: dict[int, list] = {oid: [] for oid in object_ids}
    for rel in flow_edges:
        if rel.target_id in object_ids:
            obj_id, flow_source_id = rel.target_id, rel.source_id
        elif rel.source_id in object_ids:
            obj_id, flow_source_id = rel.source_id, rel.target_id
        else:
            continue
        source_elem = by_id.get(flow_source_id)
        if source_elem is None:
            continue
        flows_by_object[obj_id].append({
            "archimate_element_id": source_elem.id,
            "name": source_elem.name,
            "archimate_type": _norm_type(source_elem.type),
        })

    # --- 3. Project one processing-activity node per object --------------------
    activities = []
    for obj in objects:
        if obj.id is None:
            raise ProvenanceError(
                f"information object {obj.name!r} has no element id — "
                "cannot anchor an Article 30 row to it"
            )
        props = _read_props(obj)
        norm = _norm_type(obj.type)

        systems = sorted(
            systems_by_object.get(obj.id, []),
            key=lambda s: (s["archimate_element_id"],),
        )

        # Collect suppliers for each system linked to this object.
        suppliers = []
        seen_vendor = set()
        for sys_entry in systems:
            for sup in suppliers_by_system.get(sys_entry["archimate_element_id"], []):
                key = (sup.get("vendor_id"), sup["name"])
                if key not in seen_vendor:
                    seen_vendor.add(key)
                    suppliers.append(sup)

        flows = sorted(
            flows_by_object.get(obj.id, []),
            key=lambda f: (f["archimate_element_id"],),
        )

        data_categories = _first(props, "data_categories", "data_category", "categories")
        if isinstance(data_categories, str):
            data_categories = [data_categories]
        elif not isinstance(data_categories, list):
            data_categories = []

        activity = {
            "node_id": f"activity_{obj.id}",
            "name": obj.name,
            # Structural provenance — REQUIRED, non-optional. This is the whole
            # point of the slice: the row is nothing without its source element.
            "provenance": {
                "origin": "element",
                "archimate_element_id": obj.id,
                "archimate_type": norm,
                "layer": _TYPE_LAYER.get(norm, "application"),
                "element_name": obj.name,
            },
            "purpose": _first(props, "purpose", "processing_purpose"),
            "lawful_basis": _first(props, "lawful_basis", "legal_basis"),
            "retention": _first(props, "retention", "retention_period"),
            "data_categories": sorted(str(c) for c in data_categories),
            "systems": systems,
            "suppliers": suppliers,
            "flows": flows,
        }
        # Hard invariant — never emit an activity without a resolvable source id.
        if activity["provenance"].get("archimate_element_id") is None:
            raise ProvenanceError(
                f"processing activity {obj.name!r} produced a null source element id"
            )
        activities.append(activity)

    slice_dict = {
        "genome_version": GENOME_VERSION,
        "slice": "data",
        "organization_id": organization_id,
        "artifact": "gdpr_article_30_ropa",
        "processing_activities": activities,
    }
    slice_dict["spec_hash"] = _spec_hash(slice_dict)
    logger.info(
        "[genome.data] org=%s activities=%s spec_hash=%s",
        organization_id,
        len(activities),
        slice_dict["spec_hash"],
    )
    return slice_dict


def _spec_hash(slice_dict: dict) -> str:
    """Deterministic fingerprint of the slice content (excludes the hash itself)."""
    payload = {k: v for k, v in slice_dict.items() if k != "spec_hash"}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str, ensure_ascii=True).encode()
    ).hexdigest()[:16]
