"""System of record for data entities, undeclared copies and master data domains.

One answer to "which application is authoritative for this data?": the
``DataEntity.system_of_record_application_id`` column, mirrored as an ArchiMate
Serving relationship (application element -> data object element). The older
free-text ``DataEntity.system_of_record`` remains only as a read-only fallback
label for rows that predate the application link.

An application "holds" an entity when it has a ``DataObject`` (an application
data object) whose name, description or table name is similar to the entity's
name/description under the platform's existing similarity search rules. Every
application that holds an entity other than its declared system of record is a
copy of it.
"""

from dataclasses import dataclass
import logging
from datetime import datetime
import re

from app import db
from app.models.application_capability import ApplicationCapabilityMapping
from app.models.application_layer import DataObject
from app.models.application_portfolio import ApplicationComponent
from app.models.business_capabilities import BusinessCapability
from app.models.process_data import DataDomain, DataEntity

logger = logging.getLogger(__name__)

GENERIC_DATA_OBJECT_TOKENS = {
    "data",
    "entity",
    "master",
    "record",
    "reference",
    "registry",
    "catalog",
    "catalogue",
    "profile",
    "details",
    "detail",
    "information",
    "info",
}


def _informative_tokens(value):
    tokens = [token for token in _search_tokens(value) if token not in GENERIC_DATA_OBJECT_TOKENS]
    return tuple(tokens) or _search_tokens(value)


class DataSorError(Exception):
    """A declaration that cannot be made; the message is shown to the user."""


@dataclass
class _DataObjectCandidate:
    id: int
    app_id: int
    object_name: str
    name: str
    description: str
    type: str = "DataObject"


def _collapse_spaces(value):
    return " ".join((value or "").split()).strip()


def _normalise_search_text(value):
    collapsed = _collapse_spaces(value).lower()
    collapsed = re.sub(r"[_\-]+", " ", collapsed)
    return re.sub(r"[^a-z0-9\s]+", " ", collapsed).strip()


def _search_tokens(value):
    return tuple(token for token in _normalise_search_text(value).split() if token)


def _entity_search_name(entity):
    return _collapse_spaces(" ".join(filter(None, [entity.name, entity.business_name, entity.technical_name])))


def _entity_search_description(entity):
    return _collapse_spaces(entity.description)


def _entity_search_variants(entity):
    variants = []
    for label in (entity.name, entity.business_name, entity.technical_name):
        value = _collapse_spaces(label)
        if value:
            variants.append({"name": value, "description": _entity_search_description(entity)})

    combined = _entity_search_name(entity)
    if combined and all(variant["name"] != combined for variant in variants):
        variants.append({"name": combined, "description": _entity_search_description(entity)})

    if not variants:
        variants.append({"name": combined or _entity_search_description(entity), "description": _entity_search_description(entity)})
    return variants


def _candidate_description(description, table_name):
    return _collapse_spaces(" ".join(filter(None, [description, table_name])))


def _holder_candidates(org_id):
    rows = (
        db.session.query(
            DataObject.id,
            DataObject.name,
            DataObject.description,
            DataObject.table_name,
            DataObject.application_component_id,
        )
        .filter(
            DataObject.organization_id == org_id,
            DataObject.application_component_id.isnot(None),
        )
        .all()
    )
    return [
        _DataObjectCandidate(
            id=row.id,
            app_id=row.application_component_id,
            object_name=row.name or row.table_name or "—",
            name=_collapse_spaces(row.name or row.table_name),
            description=_candidate_description(row.description, row.table_name),
        )
        for row in rows
        if row.application_component_id is not None and _collapse_spaces(row.name or row.table_name)
    ]


def _allowed_holder_tokens(entity, variant):
    return set(_search_tokens(entity.name)) | set(_search_tokens(entity.business_name)) | set(
        _search_tokens(entity.technical_name)
    ) | set(_search_tokens(entity.description)) | set(_search_tokens(variant["name"])) | set(
        _search_tokens(variant["description"])
    ) | GENERIC_DATA_OBJECT_TOKENS


def _bounded_token_match(candidate_tokens, required_tokens, allowed_tokens):
    if not required_tokens:
        return False
    candidate_set = set(candidate_tokens)
    required_set = set(required_tokens)
    if not required_set.issubset(candidate_set):
        return False
    return (candidate_set - required_set).issubset(allowed_tokens)


def _candidate_match_score(candidate, variant, allowed_tokens):
    variant_name = _normalise_search_text(variant["name"])
    variant_description = _normalise_search_text(variant["description"])
    candidate_name = _normalise_search_text(candidate.name)
    candidate_description = _normalise_search_text(candidate.description)
    token_sets = []
    for source in (variant["name"], variant["description"]):
        informative = _informative_tokens(source)
        if informative and informative not in token_sets:
            token_sets.append(informative)

    if variant_name and candidate_name == variant_name:
        return 400
    if variant_name and candidate_description == variant_name:
        return 350
    if variant_description and candidate_name == variant_description:
        return 325

    for required_tokens in token_sets:
        if _bounded_token_match(_search_tokens(candidate.name), required_tokens, allowed_tokens):
            return 200 + len(required_tokens)
        if _bounded_token_match(_search_tokens(candidate.description), required_tokens, allowed_tokens):
            return 150 + len(required_tokens)
    return None


def _holder_matches_for_entity(entity, candidates):
    best_by_app = {}
    for variant in _entity_search_variants(entity):
        allowed_tokens = _allowed_holder_tokens(entity, variant)
        for candidate in candidates:
            score = _candidate_match_score(candidate, variant, allowed_tokens)
            if score is None:
                continue
            current = best_by_app.get(candidate.app_id)
            if current is None or score > current["score"]:
                best_by_app[candidate.app_id] = {
                    "object_name": candidate.object_name,
                    "score": score,
                }
    return best_by_app


def _holders_by_entity(org_id, entities):
    """{entity_id: {application_id: matching data object name}} for one organisation."""
    if not entities:
        return {}
    candidates = _holder_candidates(org_id)
    result = {}
    for entity in entities:
        holders = _holder_matches_for_entity(entity, candidates)
        result[entity.id] = {app_id: row["object_name"] for app_id, row in holders.items()}
    return result


def _application_names(org_id, app_ids):
    if not app_ids:
        return {}
    rows = (
        db.session.query(ApplicationComponent.id, ApplicationComponent.name)
        .filter(
            ApplicationComponent.organization_id == org_id,
            ApplicationComponent.id.in_(list(app_ids)),
        )
        .all()
    )
    return dict(rows)


def get_application(org_id, application_id):
    """The application, only if it belongs to this organisation."""
    if not application_id:
        return None
    return (
        db.session.query(ApplicationComponent)
        .filter(
            ApplicationComponent.id == application_id,
            ApplicationComponent.organization_id == org_id,
        )
        .first()
    )


def get_entity(org_id, entity_id):
    return (
        db.session.query(DataEntity)
        .filter(DataEntity.id == entity_id, DataEntity.organization_id == org_id)
        .first()
    )


def get_domain(org_id, domain_id):
    return (
        db.session.query(DataDomain)
        .filter(DataDomain.id == domain_id, DataDomain.organization_id == org_id)
        .first()
    )


def _application_picker_query(org_id, search_text):
    term = _collapse_spaces(search_text)
    if not term:
        return []
    search_pattern = f"%{term}%"
    return (
        db.session.query(ApplicationComponent)
        .filter(
            ApplicationComponent.organization_id == org_id,
            (ApplicationComponent.name.ilike(search_pattern))
            | (ApplicationComponent.description.ilike(search_pattern))
            | (ApplicationComponent.business_domain.ilike(search_pattern))
        )
        .order_by(ApplicationComponent.name)
        .limit(10)
        .all()
    )


def _resolve_legacy_system_of_record_application(org_id, legacy_label):
    label = _collapse_spaces(legacy_label)
    if not label:
        return None
    exact = (
        db.session.query(ApplicationComponent)
        .filter(
            ApplicationComponent.organization_id == org_id,
            ApplicationComponent.name.ilike(label),
        )
        .order_by(ApplicationComponent.name)
        .all()
    )
    if len(exact) == 1:
        return exact[0]
    matches = _application_picker_query(org_id, label)
    if len(matches) == 1 and _collapse_spaces(matches[0].name).lower() == label.lower():
        return matches[0]
    return None


def backfill_system_of_record_application_links(org_id, entity_ids=None):
    query = db.session.query(DataEntity).filter(
        DataEntity.organization_id == org_id,
        DataEntity.system_of_record_application_id.is_(None),
        DataEntity.system_of_record.isnot(None),
    )
    if entity_ids:
        query = query.filter(DataEntity.id.in_(list(entity_ids)))

    changed = False
    for entity in query.all():
        application = _resolve_legacy_system_of_record_application(org_id, entity.system_of_record)
        if application is None:
            continue
        entity.system_of_record_application_id = application.id
        changed = True

    if changed:
        db.session.commit()
    return changed


def _capability_consumer_app_ids(org_id, capability_id):
    if not capability_id:
        return set()
    mapped_app_ids = {
        app_id
        for (app_id,) in (
            db.session.query(ApplicationCapabilityMapping.application_component_id)
            .join(
                BusinessCapability,
                BusinessCapability.id == ApplicationCapabilityMapping.business_capability_id,
            )
            .filter(
                ApplicationCapabilityMapping.business_capability_id == capability_id,
                BusinessCapability.organization_id == org_id,
            )
            .distinct()
            .all()
        )
    }

    capability = db.session.query(BusinessCapability).filter(
        BusinessCapability.id == capability_id,
        BusinessCapability.organization_id == org_id,
    ).first()
    if capability is None or not capability.archimate_element_id:
        return mapped_app_ids

    from app.models.models import ArchiMateRelationship

    related_app_ids = {
        app_id
        for (app_id,) in (
            db.session.query(ApplicationComponent.id)
            .join(
                ArchiMateRelationship,
                ArchiMateRelationship.source_id == ApplicationComponent.archimate_element_id,
            )
            .filter(
                ApplicationComponent.organization_id == org_id,
                ArchiMateRelationship.type == "serving",
                ArchiMateRelationship.target_id == capability.archimate_element_id,
            )
            .distinct()
            .all()
        )
    }
    return mapped_app_ids | related_app_ids


def consumer_application_ids_by_entity(org_id, entities, holders_by_entity=None):
    if not entities:
        return {}
    holders_by_entity = holders_by_entity or _holders_by_entity(org_id, entities)
    result = {}
    for entity in entities:
        consumer_ids = set(holders_by_entity.get(entity.id, {}))
        consumer_ids |= _capability_consumer_app_ids(org_id, entity.owning_capability_id)
        if entity.system_of_record_application_id:
            consumer_ids.discard(entity.system_of_record_application_id)
        result[entity.id] = consumer_ids
    return result


def consumer_application_names_by_entity(org_id, entities, holders_by_entity=None):
    consumer_ids_by_entity = consumer_application_ids_by_entity(org_id, entities, holders_by_entity)
    app_ids = {app_id for app_ids in consumer_ids_by_entity.values() for app_id in app_ids}
    names = _application_names(org_id, app_ids)
    return {
        entity_id: sorted(names.get(app_id, "—") for app_id in app_ids)
        for entity_id, app_ids in consumer_ids_by_entity.items()
    }


def _serving_link_query(app_element_id, entity_element_id):
    from app.models.models import ArchiMateRelationship

    return db.session.query(ArchiMateRelationship).filter_by(
        type="serving", source_id=app_element_id, target_id=entity_element_id
    )


def declare_system_of_record(org_id, entity_id, application_id, user_id=None):
    """Declare the application that is authoritative for a data entity.

    Writes the application link and records the ArchiMate Serving relationship.
    Redeclaring replaces the previous application's relationship. Returns the
    entity.
    """
    entity = get_entity(org_id, entity_id)
    if entity is None:
        raise DataSorError("That data entity was not found.")
    application = get_application(org_id, application_id)
    if application is None:
        raise DataSorError("Pick an application from your portfolio.")
    if not application.archimate_element_id:
        raise DataSorError(
            "That application has no ArchiMate element yet, so the link cannot be modelled."
        )
    if not entity.archimate_element_id:
        raise DataSorError(
            "That data entity has no ArchiMate element yet, so the link cannot be modelled."
        )

    previous_id = entity.system_of_record_application_id
    if previous_id and previous_id != application.id:
        previous = get_application(org_id, previous_id)
        if previous is not None and previous.archimate_element_id:
            for stale in _serving_link_query(
                previous.archimate_element_id, entity.archimate_element_id
            ).all():
                db.session.delete(stale)

    if not _serving_link_query(application.archimate_element_id, entity.archimate_element_id).first():
        from app.models.models import ArchiMateElement
        from app.modules.architecture.services.archimate_relationship_service import (
            ArchiMateRelationshipService,
        )

        source = db.session.get(ArchiMateElement, application.archimate_element_id)
        target = db.session.get(ArchiMateElement, entity.archimate_element_id)
        link = ArchiMateRelationshipService.create_relationship(
            source, target, "serving", entity.architecture_id
        )
        if link is None:
            raise DataSorError("The ArchiMate relationship could not be recorded.")

    entity.system_of_record_application_id = application.id
    entity.system_of_record_declared_by_id = user_id
    entity.system_of_record_declared_at = datetime.utcnow()
    db.session.commit()
    return entity


def declare_golden_source(org_id, domain_id, application_id):
    """Set (or, with no application, clear) the golden source of a data domain."""
    domain = get_domain(org_id, domain_id)
    if domain is None:
        raise DataSorError("That data domain was not found.")
    if application_id:
        application = get_application(org_id, application_id)
        if application is None:
            raise DataSorError("Pick an application from your portfolio.")
        domain.golden_source_application_id = application.id
    else:
        domain.golden_source_application_id = None
    db.session.commit()
    return domain


def entity_holders(org_id, entity):
    """Applications holding this entity, each labelled by their role.

    role is ``system_of_record`` for the declared application, ``copy`` for any
    other holder once one is declared, and ``undeclared`` while none is.
    """
    holders = _holders_by_entity(org_id, [entity]).get(entity.id, {})
    names = _application_names(org_id, set(holders) | {entity.system_of_record_application_id})
    declared = entity.system_of_record_application_id
    rows = []
    for app_id, object_name in holders.items():
        if declared and app_id == declared:
            role = "system_of_record"
        elif declared:
            role = "copy"
        else:
            role = "undeclared"
        rows.append(
            {
                "application_id": app_id,
                "application_name": names.get(app_id, "—"),
                "data_object_name": object_name,
                "role": role,
            }
        )
    rows.sort(key=lambda r: (r["role"] != "system_of_record", r["application_name"]))
    return rows


def list_entities(org_id):
    """Every entity in the organisation with its declared system of record."""
    backfill_system_of_record_application_links(org_id)
    entities = (
        db.session.query(DataEntity)
        .filter(DataEntity.organization_id == org_id)
        .order_by(DataEntity.name)
        .all()
    )
    names = _application_names(org_id, {e.system_of_record_application_id for e in entities})
    return [
        {
            "entity": e,
            "system_of_record_name": names.get(e.system_of_record_application_id),
            "system_of_record_fallback": e.system_of_record if not e.system_of_record_application_id else None,
        }
        for e in entities
    ]


def undeclared_copies(org_id):
    """Entities held by more than one application with no declared system of record.

    Ranked by the number of consumer applications evidenced by holder and
    capability/application relationship data.
    """
    backfill_system_of_record_application_links(org_id)
    entities = (
        db.session.query(DataEntity)
        .filter(
            DataEntity.organization_id == org_id,
            DataEntity.system_of_record_application_id.is_(None),
        )
        .all()
    )
    holders = _holders_by_entity(org_id, entities)
    flagged = [e for e in entities if len(holders.get(e.id, {})) > 1]
    names = _application_names(
        org_id, {app_id for e in flagged for app_id in holders[e.id]}
    )
    consumer_names = consumer_application_names_by_entity(org_id, flagged, holders)
    rows = [
        {
            "entity": e,
            "applications": sorted(names.get(a, "—") for a in holders[e.id]),
            "consumer_count": len(consumer_names.get(e.id, [])),
        }
        for e in flagged
    ]
    rows.sort(key=lambda r: (-r["consumer_count"], r["entity"].name))
    return rows


def master_domains(org_id):
    """Master data domains with golden source, entities and their consumer applications."""
    backfill_system_of_record_application_links(org_id)
    domains = (
        db.session.query(DataDomain)
        .filter(DataDomain.organization_id == org_id, DataDomain.domain_type == "master")
        .order_by(DataDomain.name)
        .all()
    )
    names = _application_names(org_id, {d.golden_source_application_id for d in domains})
    entities = [e for d in domains for e in d.entities]
    holders = _holders_by_entity(org_id, entities)
    consumer_names = consumer_application_names_by_entity(org_id, entities, holders)
    return [
        {
            "domain": d,
            "golden_source_name": names.get(d.golden_source_application_id),
            "entities": [
                {"entity": e, "consumers": consumer_names.get(e.id, [])}
                for e in sorted(d.entities, key=lambda e: e.name)
            ],
        }
        for d in domains
    ]
