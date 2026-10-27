"""Fact store: record and apply integration facts.

Records facts derived from external systems and applies them through the
canonical writers (backbone, relationship, ownership).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from app import db
from app.integrations.model_mapping import ElementFact, ModelMapping, OwnerFact, RelationshipFact, SourceRef
from app.models.integration_fact import IntegrationFact, IntegrationFactStatus
from app.modules.architecture.services.archimate_relationship_service import ArchiMateRelationshipService
from app.services.archimate_backbone import create_backbone_element
from app.services.element_ownership_service import set_element_owner

logger = logging.getLogger(__name__)


def record_facts(
    *,
    mapping: ModelMapping,
    item: Dict,
    organization_id: int,
    context: Optional[Dict] = None,
    commit: bool = True,
) -> List[IntegrationFact]:
    """Record facts from an external item.

    Maps the item through the mapping, creates IntegrationFact records for each fact,
    and returns the list of created facts.

    Args:
        mapping: The ModelMapping instance
        item: The external item
        organization_id: The organization
        context: Additional mapping context
        commit: Whether to commit the transaction

    Returns:
        The facts newly proposed by this read. A fact already on record for the
        item (pending, applied or rejected) is not proposed again.
    """
    facts_dict = mapping.map_item(item, organization_id, context=context)
    source = facts_dict.get("source")
    if not isinstance(source, SourceRef):
        raise ValueError("Mapping must return 'source' as SourceRef")

    facts: List[IntegrationFact] = []

    def propose(path: str, fact_type: str, data: Dict[str, Any]) -> None:
        fact = _propose(organization_id=organization_id, source=source, path=path, fact_type=fact_type, data=data)
        if fact is not None:
            facts.append(fact)

    for path, fact in facts_dict.items():
        if isinstance(fact, ElementFact):
            propose(path, "element", {
                "archimate_type": fact.archimate_type,
                "name": fact.name,
                "description": fact.description,
                "properties": fact.properties,
            })

    for idx, fact in enumerate(facts_dict.get("relationships", [])):
        if isinstance(fact, RelationshipFact):
            propose(f"relationships.{idx}", "relationship", {
                "type": fact.type,
                "source_path": fact.source_path,
                "target_path": fact.target_path,
                "description": fact.description,
                "properties": fact.properties,
            })

    for idx, fact in enumerate(facts_dict.get("owners", [])):
        if isinstance(fact, OwnerFact):
            propose(f"owners.{idx}", "owner", {
                "element_path": fact.element_path,
                "owner_name": fact.owner_name,
                "owner_email": fact.owner_email,
                "ownership_type": fact.ownership_type,
                "is_team": fact.is_team,
            })

    if commit:
        db.session.commit()

    return facts


def _propose(
    *,
    organization_id: int,
    source: SourceRef,
    path: str,
    fact_type: str,
    data: Dict[str, Any],
) -> Optional[IntegrationFact]:
    """Record one fact as pending, once.

    A fact already on record for this item and path is never proposed again,
    whatever its state: an applied fact is in the model, a rejected one was
    refused, and a pending one is already waiting for review. A pending fact
    whose content changed since the last read is refreshed in place rather than
    duplicated. Returns the new fact, or None when nothing new was proposed.
    """
    payload = json.dumps(data, sort_keys=True)
    existing = IntegrationFact.query.filter_by(
        organization_id=organization_id,
        system=source.system,
        external_id=source.external_id,
        path=path,
    ).first()
    if existing is not None:
        if existing.status == IntegrationFactStatus.PENDING and existing.fact_data != payload:
            existing.fact_data = payload
            existing.source_url = source.url
            existing.source_read_at = source.read_at
        return None

    fact = IntegrationFact(
        organization_id=organization_id,
        system=source.system,
        external_id=source.external_id,
        path=path,
        fact_type=fact_type,
        fact_data=payload,
        source_url=source.url,
        source_item_type=source.item_type,
        source_read_at=source.read_at,
        status=IntegrationFactStatus.PENDING,
    )
    db.session.add(fact)
    return fact


# ArchiMate type -> backbone layer for the types a mapping may produce.
_LAYERS = {
    "ApplicationComponent": "application",
    "ApplicationInterface": "application",
    "ApplicationService": "application",
    "BusinessActor": "business",
    "BusinessRole": "business",
    "BusinessCapability": "business",
    "TechnologyNode": "technology",
    "TechnologyService": "technology",
}


def _provenance(fact: IntegrationFact, *, json_safe: bool = False) -> Dict[str, Any]:
    """Where an applied fact came from, in the shape the writers record."""
    recorded_at = fact.source_read_at
    if json_safe and recorded_at is not None:
        recorded_at = recorded_at.isoformat()
    return {
        "source_kind": "integration",
        "source_ref": f"{fact.system}:{fact.external_id}:{fact.path}",
        "source_label": f"{fact.system} {fact.source_item_type}",
        "source_recorded_at": recorded_at,
    }


def apply_fact(fact: IntegrationFact, *, commit: bool = True) -> bool:
    """Apply one integration fact through the canonical writers.

    The writes happen inside a savepoint, so a fact that fails part-way leaves
    nothing behind and stays pending.

    Args:
        fact: The IntegrationFact to apply
        commit: Whether to commit the transaction

    Returns:
        True if the fact was applied successfully, False otherwise
    """
    if fact.status != IntegrationFactStatus.PENDING:
        logger.warning(f"Not applying fact {fact.id}: it is {fact.status.value}, not pending")
        return False
    try:
        with db.session.begin_nested():
            applied = _apply(fact)
        if not applied:
            return False
        if commit:
            db.session.commit()
        return True
    except Exception as e:
        logger.error(f"Error applying fact {fact.id}: {e}", exc_info=True)
        if commit:
            db.session.rollback()
        return False


def _apply(fact: IntegrationFact) -> bool:
    fact_data = json.loads(fact.fact_data)

    if fact.fact_type == "element":
        archimate_type = fact_data["archimate_type"]
        element = create_backbone_element(
            element_type=archimate_type,
            layer=_LAYERS.get(archimate_type, "application"),
            name=fact_data["name"],
            description=fact_data.get("description"),
            organization_id=fact.organization_id,
            provenance={
                **(fact_data.get("properties") or {}),
                **_provenance(fact, json_safe=True),
                "source_url": fact.source_url,
            },
        )
        fact.applied_element_id = element.id
        fact.status = IntegrationFactStatus.APPLIED
        fact.applied_at = datetime.utcnow()
        logger.info(f"Applied element fact {fact.id}: {element.name}")
        return True

    if fact.fact_type == "relationship":
        source_elem = _resolved_element(fact, fact_data["source_path"])
        target_elem = _resolved_element(fact, fact_data["target_path"])
        if source_elem is None or target_elem is None:
            logger.warning(f"Cannot apply relationship fact {fact.id}: missing source or target")
            return False
        relationship = ArchiMateRelationshipService.create_relationship(
            source_element=source_elem,
            target_element=target_elem,
            relationship_type=fact_data["type"],
            architecture_id=None,
            properties={"description": fact_data.get("description")},
            organization_id=fact.organization_id,
            rollback=False,
            provenance=_provenance(fact),
        )
        if relationship is None:
            logger.warning(f"Failed to apply relationship fact {fact.id}")
            return False
        fact.applied_relationship_id = relationship.id
        fact.status = IntegrationFactStatus.APPLIED
        fact.applied_at = datetime.utcnow()
        logger.info(f"Applied relationship fact {fact.id}")
        return True

    if fact.fact_type == "owner":
        element = _resolved_element(fact, fact_data["element_path"])
        if element is None:
            logger.warning(f"Cannot apply owner fact {fact.id}: missing element")
            return False

        if fact_data.get("is_team"):
            team = _team_element(fact, fact_data["owner_name"])
            fact.applied_relationship_id = _assign_team(fact, team, element)
            fact.applied_element_id = team.id
            logger.info(f"Applied team owner fact {fact.id}: {fact_data['owner_name']}")
        else:
            if not _record_person_owner(fact, fact_data, element):
                return False
            logger.info(f"Applied owner fact {fact.id}")
        fact.status = IntegrationFactStatus.APPLIED
        fact.applied_at = datetime.utcnow()
        return True

    logger.warning(f"Cannot apply fact {fact.id}: unknown fact type {fact.fact_type!r}")
    return False


def _team_element(fact: IntegrationFact, team_name: str):
    """The BusinessActor for a team the connector reported, created once.

    A team that owns several items is one actor: an earlier fact of this
    system already created it, so it is found by the reference that fact left
    on it rather than created again.
    """
    from app.models.models import ArchiMateElement

    reference = f"{fact.system}:team:{team_name}"
    for candidate in ArchiMateElement.query.filter_by(
        organization_id=fact.organization_id, type="BusinessActor", name=team_name,
    ):
        if (candidate.custom_properties or {}).get("source_ref") == reference:
            return candidate
    return create_backbone_element(
        element_type="BusinessActor",
        layer="business",
        name=team_name,
        description=f"Team from {fact.system}",
        organization_id=fact.organization_id,
        provenance={
            "source_kind": "integration",
            "source_ref": reference,
            "source_label": f"{fact.system} team",
            "source_recorded_at": fact.source_read_at.isoformat() if fact.source_read_at else None,
            "source_url": fact.source_url,
        },
    )


def _assign_team(fact: IntegrationFact, team, element) -> int:
    """The team is assigned to the element it owns, unless it already is; the
    id of that assignment."""
    from app.models.models import ArchiMateRelationship

    already = ArchiMateRelationship.query.filter_by(
        organization_id=fact.organization_id, type="assignment", source_id=team.id, target_id=element.id,
    ).first()
    if already is not None:
        return already.id
    relationship = ArchiMateRelationshipService.create_relationship(
        source_element=team,
        target_element=element,
        relationship_type="assignment",
        architecture_id=None,
        organization_id=fact.organization_id,
        rollback=False,
        provenance=_provenance(fact),
    )
    if relationship is None:
        raise ValueError(f"Cannot assign team {team.name!r} to {element.name!r}")
    return relationship.id


def _record_person_owner(fact: IntegrationFact, fact_data: Dict[str, Any], element) -> bool:
    """Record a person as owner of the application the element is the mirror of.

    Person owners are recorded against the portfolio row, not the element, so an
    element with no portfolio row has nowhere to record one.
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.services.element_ownership_service import resolve_user_by_name

    user = resolve_user_by_name(fact_data.get("owner_email") or fact_data["owner_name"], fact.organization_id)
    if user is None:
        logger.warning(f"Cannot apply owner fact {fact.id}: user not found")
        return False
    application = ApplicationComponent.query.filter_by(
        archimate_element_id=element.id, organization_id=fact.organization_id,
    ).first()
    if application is None:
        logger.warning(f"Cannot apply owner fact {fact.id}: element {element.id} has no portfolio application")
        return False
    set_element_owner(
        element_type="application",
        element_id=application.id,
        user_id=user.id,
        organization_id=fact.organization_id,
        ownership_type=fact_data.get("ownership_type", "primary"),
        commit=False,
    )
    return True


def reject_fact(fact: IntegrationFact, *, reason: str, commit: bool = True) -> None:
    """Reject an integration fact.

    Args:
        fact: The IntegrationFact to reject
        reason: Why the fact was rejected
        commit: Whether to commit the transaction
    """
    fact.status = IntegrationFactStatus.REJECTED
    fact.rejection_reason = reason
    if commit:
        db.session.commit()


def _resolved_element(fact: IntegrationFact, path: str):
    """The element an applied element fact of the same item at *path* created."""
    from app.models.models import ArchiMateElement

    element_fact = IntegrationFact.query.filter_by(
        organization_id=fact.organization_id,
        system=fact.system,
        external_id=fact.external_id,
        path=path,
        fact_type="element",
        status=IntegrationFactStatus.APPLIED,
    ).first()
    if element_fact is None or element_fact.applied_element_id is None:
        return None
    return ArchiMateElement.query.filter_by(
        id=element_fact.applied_element_id, organization_id=fact.organization_id,
    ).first()


def sources_for_elements(element_ids: Iterable[int], organization_id: int) -> Dict[int, Dict[str, Optional[str]]]:
    """Where each connector-derived element came from.

    ``{element_id: {system, item, url, read_at}}`` for every element in the
    organisation that an applied integration fact created; an element nobody
    derived from a connector is absent. ``item`` is the name of the external
    item (the repository, for GitHub) and ``read_at`` is ISO text. The earliest
    fact that created an element is its source.
    """
    ids = {i for i in element_ids if isinstance(i, int)}
    if not ids or organization_id is None:
        return {}
    facts = (
        IntegrationFact.query.filter(
            IntegrationFact.organization_id == organization_id,
            IntegrationFact.applied_element_id.in_(ids),
            IntegrationFact.status == IntegrationFactStatus.APPLIED,
        )
        .order_by(IntegrationFact.id)
        .all()
    )
    if not facts:
        return {}
    primaries = {
        (f.system, f.external_id): json.loads(f.fact_data).get("name")
        for f in IntegrationFact.query.filter(
            IntegrationFact.organization_id == organization_id,
            IntegrationFact.external_id.in_({f.external_id for f in facts}),
            IntegrationFact.fact_type == "element",
            IntegrationFact.path == "element",
        )
    }
    sources: Dict[int, Dict[str, Optional[str]]] = {}
    for fact in facts:
        if fact.applied_element_id in sources:
            continue
        url = fact.source_url if (fact.source_url or "").lower().startswith(("https://", "http://")) else None
        sources[fact.applied_element_id] = {
            "system": fact.system,
            "item": primaries.get((fact.system, fact.external_id)) or fact.external_id,
            "url": url,
            "read_at": fact.source_read_at.isoformat() if fact.source_read_at else None,
        }
    return sources


def sources_for_relationships(
    relationship_ids: Iterable[int], organization_id: int
) -> Dict[int, Dict[str, Optional[str]]]:
    """Where each connector-written link came from.

    ``{relationship_id: {system, item, url, read_at, ...}}`` for every link in
    the organisation that an applied integration fact wrote, or that a
    connector stamped with its origin when it wrote the link (``source_kind``
    ``connector``: the repository, commit and file it was read at). A stamped
    link also carries ``repository``, ``commit``, ``file``, ``detail``,
    ``label`` and ``recorded_at``, and its ``url`` is the file at that commit;
    where both exist, the stamp's details are added to the fact's. A link no
    connector wrote is absent.
    """
    from app.models import ArchiMateRelationship

    ids = {i for i in relationship_ids if isinstance(i, int)}
    if not ids or organization_id is None:
        return {}
    sources: Dict[int, Dict[str, Optional[str]]] = {}
    facts = (
        IntegrationFact.query.filter(
            IntegrationFact.organization_id == organization_id,
            IntegrationFact.applied_relationship_id.in_(ids),
            IntegrationFact.status == IntegrationFactStatus.APPLIED,
        )
        .order_by(IntegrationFact.id)
        .all()
    )
    if facts:
        primaries = {
            (f.system, f.external_id): json.loads(f.fact_data).get("name")
            for f in IntegrationFact.query.filter(
                IntegrationFact.organization_id == organization_id,
                IntegrationFact.external_id.in_({f.external_id for f in facts}),
                IntegrationFact.fact_type == "element",
                IntegrationFact.path == "element",
            )
        }
        for fact in facts:
            if fact.applied_relationship_id in sources:
                continue
            url = fact.source_url if (fact.source_url or "").lower().startswith(("https://", "http://")) else None
            sources[fact.applied_relationship_id] = {
                "system": fact.system,
                "item": primaries.get((fact.system, fact.external_id)) or fact.external_id,
                "url": url,
                "read_at": fact.source_read_at.isoformat() if fact.source_read_at else None,
            }

    stamped = db.session.execute(
        db.select(
            ArchiMateRelationship.id,
            ArchiMateRelationship.source_ref,
            ArchiMateRelationship.source_label,
            ArchiMateRelationship.source_recorded_at,
        ).where(
            ArchiMateRelationship.id.in_(ids),
            ArchiMateRelationship.organization_id == organization_id,
            ArchiMateRelationship.source_kind == "connector",
        )
    ).all()
    for rel_id, ref, label, recorded_at in stamped:
        origin = _stamped_origin(ref, label, recorded_at)
        sources[rel_id] = {**sources.get(rel_id, {}), **{k: v for k, v in origin.items() if v is not None}}
        for key in ("system", "item", "url", "read_at"):
            sources[rel_id].setdefault(key, None)
    return sources


def _stamped_origin(ref: Optional[str], label: Optional[str], recorded_at) -> Dict[str, Optional[str]]:
    """The origin a connector stamped on a link it wrote, in the citation's shape."""
    from app.connectors.github import file_url as github_file_url

    try:
        body = json.loads(ref or "{}")
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    read_at = recorded_at.isoformat() if recorded_at else None
    system = body.get("system")
    return {
        "system": system,
        "item": body.get("repository"),
        "url": github_file_url(body.get("repository"), body.get("commit"), body.get("file"))
        if system == "github"
        else None,
        "read_at": read_at,
        "repository": body.get("repository"),
        "commit": body.get("commit"),
        "file": body.get("file"),
        "detail": body.get("detail"),
        "label": label,
        "recorded_at": read_at,
    }


def citation_sources(
    *, relationship_ids: Iterable[int], element_ids: Iterable[int], organization_id: int
) -> Dict[str, Dict[int, Dict[str, Optional[str]]]]:
    """The one place a citation learns where a fact came from (ADR-002).

    ``{"relationships": {id: source}, "elements": {id: source}}``: a link cites
    its own source when a connector wrote it; anything else that rests on a
    connector-derived element cites that element's source. Two batched reads per
    kind, fenced to the organisation.
    """
    return {
        "relationships": sources_for_relationships(relationship_ids, organization_id),
        "elements": sources_for_elements(element_ids, organization_id),
    }
