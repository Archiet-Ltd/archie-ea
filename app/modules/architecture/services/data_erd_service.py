"""The logical data model of a data domain, as an entity relationship diagram.

One store: an entity is the existing DataEntity record, linked to its ArchiMate
data object through archimate_element_id. Its attributes and the relationships
between entities hang off it (DataEntityAttribute, DataEntityRelationship).
Nothing here keeps a second copy of an entity.
"""

from __future__ import annotations

from app import db
from app.models.archimate_core import ArchiMateElement
from app.models.process_data import (
    ERD_CARDINALITIES,
    DataEntity,
    DataEntityAttribute,
    DataEntityRelationship,
)

# The pairs a data architect names ("one to many") and the ends they set.
PRESETS = {
    "one-to-one": ("exactly_one", "exactly_one"),
    "one-to-many": ("exactly_one", "zero_or_many"),
    "many-to-one": ("zero_or_many", "exactly_one"),
    "many-to-many": ("zero_or_many", "zero_or_many"),
}

DATA_TYPES = (
    "uuid", "integer", "bigint", "decimal", "varchar", "text", "boolean", "date", "timestamp", "json",
)

CARDINALITY_LABELS = {
    "exactly_one": "Exactly one",
    "zero_or_one": "Zero or one",
    "one_or_many": "One or many",
    "zero_or_many": "Zero or many",
}


class ErdError(ValueError):
    """The request cannot be applied; the message is shown to the person."""


def _clean_attributes(raw):
    rows = []
    seen = set()
    for index, item in enumerate(raw or []):
        name = str((item or {}).get("name") or "").strip()
        if not name:
            continue
        if name.lower() in seen:
            raise ErdError(f"The attribute “{name}” appears twice. Attribute names must be different.")
        seen.add(name.lower())
        rows.append(
            {
                "id": item.get("id") if isinstance(item.get("id"), int) else None,
                "name": name[:200],
                "data_type": (str(item.get("data_type") or "").strip() or None),
                "is_primary_key": bool(item.get("is_primary_key")),
                "is_foreign_key": bool(item.get("is_foreign_key")),
                "is_nullable": bool(item.get("is_nullable", True)) and not bool(item.get("is_primary_key")),
                "position": index,
            }
        )
    return rows


def _apply_attributes(entity, rows):
    existing = {a.id: a for a in entity.attributes}
    keep = set()
    for row in rows:
        attr = existing.get(row["id"]) if row["id"] else None
        if attr is None:
            attr = DataEntityAttribute(entity_id=entity.id, organization_id=entity.organization_id)
            db.session.add(attr)
        else:
            keep.add(attr.id)
        for key in ("name", "data_type", "is_primary_key", "is_foreign_key", "is_nullable", "position"):
            setattr(attr, key, row[key])
    for attr_id, attr in existing.items():
        if attr_id not in keep:
            db.session.delete(attr)


def attribute_dict(attr):
    return {
        "id": attr.id,
        "name": attr.name,
        "data_type": attr.data_type,
        "is_primary_key": bool(attr.is_primary_key),
        "is_foreign_key": bool(attr.is_foreign_key),
        "is_nullable": bool(attr.is_nullable),
    }


def entity_dict(entity):
    return {
        "id": entity.id,
        "name": entity.name,
        "description": entity.description,
        "domain_id": entity.domain_id,
        "archimate_element_id": entity.archimate_element_id,
        "attributes": [attribute_dict(a) for a in entity.attributes],
        "url": f"/architecture/data-entities/{entity.id}",
    }


def relationship_dict(rel):
    return {
        "id": rel.id,
        "source_id": rel.source_entity_id,
        "target_id": rel.target_entity_id,
        "label": rel.label,
        "source_cardinality": rel.source_cardinality,
        "target_cardinality": rel.target_cardinality,
    }


def domain_diagram(domain):
    """Everything the diagram of one domain draws."""
    entities = (
        DataEntity.query.filter(
            DataEntity.domain_id == domain.id, DataEntity.organization_id == domain.organization_id
        )
        .order_by(DataEntity.name)
        .all()
    )
    ids = [e.id for e in entities]
    rels = []
    if ids:
        rels = (
            DataEntityRelationship.query.filter(
                DataEntityRelationship.organization_id == domain.organization_id,
                DataEntityRelationship.source_entity_id.in_(ids),
                DataEntityRelationship.target_entity_id.in_(ids),
            )
            .order_by(DataEntityRelationship.id)
            .all()
        )
    return {
        "domain": {"id": domain.id, "name": domain.name, "description": domain.description},
        "entities": [entity_dict(e) for e in entities],
        "relationships": [relationship_dict(r) for r in rels],
        "cardinalities": [{"value": v, "label": CARDINALITY_LABELS[v]} for v in ERD_CARDINALITIES],
    }


def create_entity(domain, name, attributes, description=None):
    name = (name or "").strip()
    if not name:
        raise ErdError("Give the entity a name.")
    rows = _clean_attributes(attributes)
    clash = DataEntity.query.filter(
        DataEntity.organization_id == domain.organization_id,
        DataEntity.domain_id == domain.id,
        db.func.lower(DataEntity.name) == name.lower(),
    ).first()
    if clash is not None:
        raise ErdError(f"This domain already has an entity called “{name}”.")
    entity = DataEntity(
        name=name[:200],
        description=(description or "").strip() or None,
        domain_id=domain.id,
        organization_id=domain.organization_id,
    )
    db.session.add(entity)
    db.session.flush()
    _apply_attributes(entity, rows)
    db.session.flush()
    db.session.refresh(entity)
    return entity


def update_entity(entity, name, attributes, description=None):
    name = (name or "").strip()
    if not name:
        raise ErdError("Give the entity a name.")
    rows = _clean_attributes(attributes)
    clash = DataEntity.query.filter(
        DataEntity.organization_id == entity.organization_id,
        DataEntity.domain_id == entity.domain_id,
        DataEntity.id != entity.id,
        db.func.lower(DataEntity.name) == name.lower(),
    ).first()
    if clash is not None:
        raise ErdError(f"This domain already has an entity called “{name}”.")
    entity.name = name[:200]
    if description is not None:
        entity.description = description.strip() or None
    if entity.archimate_element_id:
        element = db.session.get(ArchiMateElement, entity.archimate_element_id)
        if element is not None:
            element.name = entity.name
            if description is not None:
                element.description = entity.description or f"Data object for entity: {entity.name}"
    _apply_attributes(entity, rows)
    db.session.flush()
    db.session.refresh(entity)
    return entity


def relate(source, target, preset=None, source_cardinality=None, target_cardinality=None, label=None):
    if source.organization_id != target.organization_id:
        raise ErdError("Both entities must belong to your organisation.")
    if preset:
        if preset not in PRESETS:
            raise ErdError("Choose one of the listed relationship types.")
        source_cardinality, target_cardinality = PRESETS[preset]
    source_cardinality = source_cardinality or "exactly_one"
    target_cardinality = target_cardinality or "zero_or_many"
    if source_cardinality not in ERD_CARDINALITIES or target_cardinality not in ERD_CARDINALITIES:
        raise ErdError("Choose how many records take part at each end.")
    rel = DataEntityRelationship(
        source_entity_id=source.id,
        target_entity_id=target.id,
        organization_id=source.organization_id,
        label=(label or "").strip()[:200] or None,
        source_cardinality=source_cardinality,
        target_cardinality=target_cardinality,
    )
    db.session.add(rel)
    db.session.flush()
    return rel


def update_relationship(rel, preset=None, source_cardinality=None, target_cardinality=None, label=None):
    if preset:
        if preset not in PRESETS:
            raise ErdError("Choose one of the listed relationship types.")
        source_cardinality, target_cardinality = PRESETS[preset]
    for value in (source_cardinality, target_cardinality):
        if value is not None and value not in ERD_CARDINALITIES:
            raise ErdError("Choose how many records take part at each end.")
    if source_cardinality:
        rel.source_cardinality = source_cardinality
    if target_cardinality:
        rel.target_cardinality = target_cardinality
    if label is not None:
        rel.label = label.strip()[:200] or None
    db.session.flush()
    return rel

