"""Load a transformed Lucidchart payload into the ArchiMate repository.

One implementation, two front doors. The CLI (`flask import-lucid`) and the
import screen both call `import_payload`, because an importer that behaves
differently depending on how it was invoked is two importers, and only one of
them ever gets tested.

`preview=True` does every lookup and every comparison and writes nothing. That
is what makes a review screen possible: an architect can see exactly what would
be created, what would attach to something already in the repository, and what
would change, before any of it happens. It is also the honest way to answer
"what will re-importing this diagram do to my estate?".
"""
from app.services.change_source import records_changes_as
from datetime import datetime

from app import db


def _conformance(elements, relationships):
    """Check relationships against the ArchiMate 3.2 matrix. Reported, never enforced."""
    try:
        from app.config.archimate_relationship_matrix import (  # noqa: PLC0415
            ALL_ELEMENTS,
            get_valid_relationships,
            is_valid_relationship,
        )
    except Exception:  # noqa: BLE001
        return None

    types = {e.get("id"): e.get("type") for e in elements}
    ok = unknown = 0
    violations = []
    for rel in relationships:
        source, target = types.get(rel.get("source_id")), types.get(rel.get("target_id"))
        if not source or not target:
            continue
        if source not in ALL_ELEMENTS or target not in ALL_ELEMENTS:
            unknown += 1
            continue
        if is_valid_relationship(source, target, rel.get("type") or ""):
            ok += 1
        else:
            violations.append({
                "source_type": source, "type": rel.get("type"), "target_type": target,
                "permitted": get_valid_relationships(source, target)[:4],
            })
    return {"ok": ok, "unknown": unknown, "violations": violations}


def _container_map(elements, relationships):
    """child lucid id → containing Location name, from derived nesting."""
    by_id = {e.get("id"): e for e in elements}
    mapping = {}
    for rel in relationships:
        if rel.get("derived_from") != "nesting":
            continue
        parent = by_id.get(rel.get("source_id"))
        if parent is not None:
            mapping[rel.get("target_id")] = parent.get("name")
    return mapping


@records_changes_as("import")
def import_payload(payload, org_id, dedupe="name-type", link_applications=True,
                   create_applications=False, preview=False):
    """Persist a transformed payload, or work out what persisting it would do.

    Args:
        payload: the transformer's output — elements, relationships, warnings.
        org_id:  the tenant. Required: archimate_elements.organization_id is
                 NOT NULL, and an unscoped de-duplication lookup would reach
                 across tenants and attach this import to another customer's
                 estate.
        dedupe:  "name-type" | "name-type-container" | "none".
        preview: work everything out and write nothing.

    Returns a report dict. `elements` and `relationships` each carry a verdict
    per row — created / linked / changed / skipped — which is what the review
    screen renders and what makes a re-import legible instead of a number.
    """
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    elements = payload.get("elements") or []
    relationships = payload.get("relationships") or []
    container_of = _container_map(elements, relationships) if dedupe == "name-type-container" else {}

    report = {
        "preview": preview,
        "model_name": payload.get("model_name"),
        "warnings": list(payload.get("warnings") or []),
        "elements": [],
        "relationships": [],
        "applications": {"linked": 0, "created": 0, "already_linked": 0},
        "conformance": _conformance(elements, relationships),
        "counts": {},
    }

    lucid_to_db = {}
    element_rows = []

    # Elements this run has already decided to create, keyed the same way the
    # de-duplication is. A commit finds the second INTERNET by querying the row
    # the first one just inserted; a preview inserts nothing, so without this it
    # counts all five and promises the architect 80 elements when the answer is
    # 74. A preview that over-promises is worse than no preview.
    created_this_run = {}
    # lucid element id -> the identity it resolves to (a database id when
    # known, otherwise its de-duplication key). Relationship de-duplication
    # in preview needs this: two edges are the same edge when their
    # endpoints resolve to the same elements, not when both are None.
    canonical_of = {}

    def _dedupe_key(name, element_type, lucid_id):
        if dedupe == "none":
            return None
        if dedupe == "name-type-container":
            return (name, element_type, container_of.get(lucid_id))
        return (name, element_type)

    for element in elements:
        name, element_type = element.get("name"), element.get("type")
        if not name or not element_type:
            report["elements"].append({"name": name or "(unnamed)", "type": element_type,
                                       "verdict": "skipped",
                                       "detail": "no name or no type"})
            continue

        key = _dedupe_key(name, element_type, element.get("id"))
        if key is not None and key in created_this_run:
            # Same element twice in one diagram. The commit path would find the
            # row the first occurrence created; preview has no row to find.
            lucid_to_db[element["id"]] = created_this_run[key]
            canonical_of[element["id"]] = key
            report["elements"].append({
                "name": name, "type": element_type, "verdict": "linked",
                "detail": "duplicate within this diagram",
                "guessed": (element.get("custom_properties") or {}).get(
                    "lucid_type_source") == "fallback",
            })
            continue

        existing = None
        if dedupe != "none":
            candidates = ArchiMateElement.query.filter_by(
                organization_id=org_id, name=name, type=element_type).all()
            if dedupe == "name-type-container":
                want = container_of.get(element.get("id"))
                candidates = [c for c in candidates
                              if (c.custom_properties or {}).get("lucid_container") == want]
            existing = candidates[0] if candidates else None

        incoming_props = element.get("custom_properties") or {}

        if existing is not None:
            merged = dict(existing.custom_properties or {})
            merged.update(incoming_props)
            changed = merged != (existing.custom_properties or {})
            if changed and not preview:
                existing.custom_properties = merged
            lucid_to_db[element["id"]] = existing.id
            canonical_of[element["id"]] = existing.id
            element_rows.append((element_type, name, existing.id))
            report["elements"].append({
                "name": name, "type": element_type,
                "verdict": "changed" if changed else "linked",
                "detail": "properties refreshed" if changed
                          else "already in the repository",
                "guessed": incoming_props.get("lucid_type_source") == "fallback",
            })
            continue

        if preview:
            # No row to point at yet. Later relationships between two
            # would-be-new elements therefore cannot be resolved, and are
            # reported as "new" rather than silently dropped.
            lucid_to_db[element["id"]] = None
            canonical_of[element["id"]] = key if key is not None else element["id"]
            if key is not None:
                created_this_run[key] = None
            report["elements"].append({
                "name": name, "type": element_type, "verdict": "created",
                "detail": "would be created",
                "guessed": incoming_props.get("lucid_type_source") == "fallback",
            })
            continue

        try:
            with db.session.begin_nested():
                props = dict(incoming_props)
                if element.get("id") in container_of:
                    props["lucid_container"] = container_of[element["id"]]
                row = ArchiMateElement(
                    name=name, type=element_type,
                    layer=(element.get("layer") or "other"),
                    description=element.get("description"),
                    custom_properties=props, organization_id=org_id,
                )
                db.session.add(row)
                db.session.flush()
            lucid_to_db[element["id"]] = row.id
            canonical_of[element["id"]] = row.id
            if key is not None:
                created_this_run[key] = row.id
            element_rows.append((element_type, name, row.id))
            report["elements"].append({
                "name": name, "type": element_type, "verdict": "created",
                "detail": "", "guessed": props.get("lucid_type_source") == "fallback",
            })
        except Exception as exc:  # noqa: BLE001 — one bad row costs one row
            report["elements"].append({"name": name, "type": element_type,
                                       "verdict": "skipped", "detail": str(exc)[:120]})

    seen_relationships = set()
    for relationship in relationships:
        source = lucid_to_db.get(relationship.get("source_id"), "missing")
        target = lucid_to_db.get(relationship.get("target_id"), "missing")
        rel_type = relationship.get("type")

        if source == "missing" or target == "missing":
            report["relationships"].append({"type": rel_type, "verdict": "skipped",
                                            "detail": "an endpoint did not import",
                                            "derived_from": relationship.get("derived_from")})
            continue

        if preview and (source is None or target is None):
            # Identify each endpoint by what it will BECOME, not by the database
            # id it does not have yet. A first attempt keyed on (source, target)
            # while both were None, so every edge after the first of each type
            # looked like a duplicate: preview reported 5 relationships where
            # the commit created 94.
            key = (canonical_of.get(relationship.get("source_id")),
                   canonical_of.get(relationship.get("target_id")),
                   rel_type)
            if key in seen_relationships:
                report["relationships"].append({
                    "type": rel_type, "verdict": "linked",
                    "detail": "duplicate within this diagram",
                    "derived_from": relationship.get("derived_from")})
                continue
            seen_relationships.add(key)
            report["relationships"].append({"type": rel_type, "verdict": "created",
                                            "detail": "would be created",
                                            "derived_from": relationship.get("derived_from")})
            continue

        existing = ArchiMateRelationship.query.filter_by(
            organization_id=org_id, source_id=source, target_id=target, type=rel_type).first()
        if existing is not None:
            provenance = relationship.get("derived_from")
            backfilled = bool(provenance and not existing.derived_from)
            if backfilled and not preview:
                existing.derived_from = provenance
            report["relationships"].append({
                "type": rel_type,
                "verdict": "changed" if backfilled else "linked",
                "detail": "provenance recorded" if backfilled else "already present",
                "derived_from": provenance,
            })
            continue

        if preview:
            report["relationships"].append({"type": rel_type, "verdict": "created",
                                            "detail": "would be created",
                                            "derived_from": relationship.get("derived_from")})
            continue

        try:
            with db.session.begin_nested():
                db.session.add(ArchiMateRelationship(
                    type=rel_type, source_id=source, target_id=target,
                    description=relationship.get("description"),
                    access_mode=relationship.get("access_mode"),
                    flow_label=(relationship.get("flow_label") or None) and
                               relationship["flow_label"][:200],
                    custom_label=(relationship.get("custom_label") or None) and
                                 relationship["custom_label"][:200],
                    connection_spec=relationship.get("connection_spec") or {},
                    derived_from=relationship.get("derived_from"),
                    organization_id=org_id,
                ))
            report["relationships"].append({"type": rel_type, "verdict": "created",
                                            "detail": "",
                                            "derived_from": relationship.get("derived_from")})
        except Exception as exc:  # noqa: BLE001
            report["relationships"].append({"type": rel_type, "verdict": "skipped",
                                            "detail": str(exc)[:120],
                                            "derived_from": relationship.get("derived_from")})

    if link_applications:
        report["applications"] = _link_portfolio(
            org_id, element_rows, create_applications, preview)

    if not preview:
        db.session.commit()

    def _tally(rows):
        out = {}
        for row in rows:
            out[row["verdict"]] = out.get(row["verdict"], 0) + 1
        return out

    # Ordered, de-duplicated DB ids of every element now in this diagram, plus
    # the relationships between them — enough to render it on the composer canvas.
    seen_el = set()
    report["diagram_element_ids"] = [
        r[2] for r in element_rows
        if r[2] is not None and not (r[2] in seen_el or seen_el.add(r[2]))
    ]
    report["counts"] = {
        "elements": _tally(report["elements"]),
        "relationships": _tally(report["relationships"]),
        "guessed_types": sum(1 for e in report["elements"] if e.get("guessed")),
        "inferred_relationships": sum(1 for r in report["relationships"]
                                      if r.get("derived_from")),
    }
    return report


def _link_portfolio(org_id, element_rows, create_missing, preview):
    """Connect imported ApplicationComponents to portfolio applications."""
    from app.models.application_portfolio import ApplicationComponent as PortfolioApp

    linked = created = already = 0
    for element_type, name, element_id in element_rows:
        if element_type != "ApplicationComponent":
            continue
        app_row = PortfolioApp.query.filter_by(organization_id=org_id, name=name).first()

        if app_row is None:
            if not create_missing:
                continue
            created += 1
            if preview:
                continue
            try:
                with db.session.begin_nested():
                    db.session.add(PortfolioApp(name=name, organization_id=org_id,
                                                archimate_element_id=element_id))
            except Exception:  # noqa: BLE001
                created -= 1
            continue

        if app_row.archimate_element_id:
            already += 1
            continue
        linked += 1
        if preview or element_id is None:
            continue
        try:
            with db.session.begin_nested():
                app_row.archimate_element_id = element_id
        except Exception:  # noqa: BLE001
            linked -= 1

    return {"linked": linked, "created": created, "already_linked": already,
            "checked_at": datetime.utcnow().isoformat()}
