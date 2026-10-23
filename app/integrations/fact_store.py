"""Fact store: record and apply integration facts.

Records facts derived from external systems and applies them through the
canonical writers (backbone, relationship, ownership).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

from app import db
from app.integrations.model_mapping import ElementFact, ModelMapping, OwnerFact, RelationshipFact, SourceRef
from app.models.integration_fact import IntegrationFact, IntegrationFactStatus
from app.modules.architecture.services.archimate_relationship_service import ArchiMateRelationshipService
from app.services.archimate_backbone import create_or_update_element
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
        List of created IntegrationFact records
    """
    facts_dict = mapping.map_item(item, organization_id, context=context)
    source = facts_dict.get("source")
    if not isinstance(source, SourceRef):
        raise ValueError("Mapping must return 'source' as SourceRef")

    facts = []

    # Record element facts
    for path, fact in facts_dict.items():
        if path == "source":
            continue
        if isinstance(fact, ElementFact):
            fact_record = IntegrationFact(
                organization_id=organization_id,
                system=source.system,
                external_id=source.external_id,
                path=path,
                fact_type="element",
                fact_data=json.dumps({
                    "archimate_type": fact.archimate_type,
                    "name": fact.name,
                    "description": fact.description,
                    "properties": fact.properties,
                }),
                source_url=source.url,
                source_item_type=source.item_type,
                source_read_at=source.read_at,
                status=IntegrationFactStatus.PENDING,
            )
            db.session.add(fact_record)
            facts.append(fact_record)

    # Record relationship facts
    relationships = facts_dict.get("relationships", [])
    for idx, fact in enumerate(relationships):
        if isinstance(fact, RelationshipFact):
            fact_record = IntegrationFact(
                organization_id=organization_id,
                system=source.system,
                external_id=source.external_id,
                path=f"relationships.{idx}",
                fact_type="relationship",
                fact_data=json.dumps({
                    "type": fact.type,
                    "source_path": fact.source_path,
                    "target_path": fact.target_path,
                    "description": fact.description,
                    "properties": fact.properties,
                }),
                source_url=source.url,
                source_item_type=source.item_type,
                source_read_at=source.read_at,
                status=IntegrationFactStatus.PENDING,
            )
            db.session.add(fact_record)
            facts.append(fact_record)

    # Record owner facts
    owners = facts_dict.get("owners", [])
    for idx, fact in enumerate(owners):
        if isinstance(fact, OwnerFact):
            fact_record = IntegrationFact(
                organization_id=organization_id,
                system=source.system,
                external_id=source.external_id,
                path=f"owners.{idx}",
                fact_type="owner",
                fact_data=json.dumps({
                    "element_path": fact.element_path,
                    "owner_name": fact.owner_name,
                    "owner_email": fact.owner_email,
                    "ownership_type": fact.ownership_type,
                    "is_team": fact.is_team,
                }),
                source_url=source.url,
                source_item_type=source.item_type,
                source_read_at=source.read_at,
                status=IntegrationFactStatus.PENDING,
            )
            db.session.add(fact_record)
            facts.append(fact_record)

    if commit:
        db.session.commit()

    return facts


def apply_fact(fact: IntegrationFact, *, commit: bool = True) -> bool:
    """Apply one integration fact through the canonical writers.

    Args:
        fact: The IntegrationFact to apply
        commit: Whether to commit the transaction

    Returns:
        True if the fact was applied successfully, False otherwise
    """
    try:
        fact_data = json.loads(fact.fact_data)

        if fact.fact_type == "element":
            # Apply through archimate_backbone
            from app.services.archimate_backbone import create_backbone_element

            # Determine layer from ArchiMate type
            archimate_type = fact_data["archimate_type"]
            layer_map = {
                "ApplicationComponent": "application",
                "ApplicationInterface": "application",
                "ApplicationService": "application",
                "BusinessActor": "business",
                "BusinessRole": "business",
                "BusinessCapability": "business",
                "TechnologyNode": "technology",
                "TechnologyService": "technology",
            }
            layer = layer_map.get(archimate_type, "application")

            provenance = {
                "source_kind": "integration",
                "source_ref": f"{fact.system}:{fact.external_id}:{fact.path}",
                "source_label": f"{fact.system} {fact.source_item_type}",
                "source_recorded_at": fact.source_read_at,
                **(fact_data.get("properties") or {}),
            }

            element = create_backbone_element(
                element_type=archimate_type,
                layer=layer,
                name=fact_data["name"],
                description=fact_data.get("description"),
                organization_id=fact.organization_id,
                provenance=provenance,
            )
            fact.applied_element_id = element.id
            fact.status = IntegrationFactStatus.APPLIED
            fact.applied_at = datetime.utcnow()
            logger.info(f"Applied element fact {fact.id}: {element.name}")

        elif fact.fact_type == "relationship":
            # Resolve source and target elements
            source_elem_id = _resolve_element_id(
                fact.organization_id, fact.system, fact.external_id, fact_data["source_path"]
            )
            target_elem_id = _resolve_element_id(
                fact.organization_id, fact.system, fact.external_id, fact_data["target_path"]
            )

            if not source_elem_id or not target_elem_id:
                logger.warning(f"Cannot apply relationship fact {fact.id}: missing source or target")
                return False

            from app.models.models import ArchiMateElement

            source_elem = ArchiMateElement.query.get(source_elem_id)
            target_elem = ArchiMateElement.query.get(target_elem_id)

            if not source_elem or not target_elem:
                logger.warning(f"Cannot apply relationship fact {fact.id}: element not found")
                return False

            # Apply through ArchiMateRelationshipService
            relationship = ArchiMateRelationshipService.create_relationship(
                source_element=source_elem,
                target_element=target_elem,
                relationship_type=fact_data["type"],
                architecture_id=1,  # Default architecture
                properties={
                    "description": fact_data.get("description"),
                    **(fact_data.get("properties") or {}),
                },
                organization_id=fact.organization_id,
                provenance={
                    "source_kind": "integration",
                    "source_ref": f"{fact.system}:{fact.external_id}:{fact.path}",
                    "source_label": f"{fact.system} {fact.source_item_type}",
                    "source_recorded_at": fact.source_read_at,
                },
            )
            if relationship:
                fact.status = IntegrationFactStatus.APPLIED
                fact.applied_at = datetime.utcnow()
                logger.info(f"Applied relationship fact {fact.id}")
            else:
                logger.warning(f"Failed to apply relationship fact {fact.id}")
                return False

        elif fact.fact_type == "owner":
            # Resolve the element
            element_id = _resolve_element_id(
                fact.organization_id, fact.system, fact.external_id, fact_data["element_path"]
            )
            if not element_id:
                logger.warning(f"Cannot apply owner fact {fact.id}: missing element")
                return False

            if fact_data.get("is_team"):
                # Create or find BusinessActor for team
                team_elem = create_or_update_element(
                    organization_id=fact.organization_id,
                    element_type="BusinessActor",
                    name=fact_data["owner_name"],
                    description=f"Team from {fact.system}",
                    source_kind="integration",
                    source_ref=f"{fact.system}:team:{fact_data['owner_name']}",
                    source_label=f"{fact.system} team",
                    source_recorded_at=fact.source_read_at,
                )
                # TODO: Create Assignment relationship from team to element
                fact.status = IntegrationFactStatus.APPLIED
                fact.applied_at = datetime.utcnow()
                logger.info(f"Applied team owner fact {fact.id}: {fact_data['owner_name']}")
            else:
                # Resolve user by name/email
                from app.services.element_ownership_service import resolve_user_by_name

                owner_identifier = fact_data.get("owner_email") or fact_data["owner_name"]
                user = resolve_user_by_name(owner_identifier, fact.organization_id)
                if not user:
                    logger.warning(f"Cannot apply owner fact {fact.id}: user not found")
                    return False

                # Determine element type
                from app.models.models import ArchiMateElement
                element = ArchiMateElement.query.get(element_id)
                element_type_map = {
                    "ApplicationComponent": "application",
                    "BusinessCapability": "capability",
                }
                element_type = element_type_map.get(element.type, "application")

                # Apply through element_ownership_service
                set_element_owner(
                    element_type=element_type,
                    element_id=element_id,
                    user_id=user.id,
                    organization_id=fact.organization_id,
                    ownership_type=fact_data.get("ownership_type", "primary"),
                    commit=False,
                )
                fact.status = IntegrationFactStatus.APPLIED
                fact.applied_at = datetime.utcnow()
                logger.info(f"Applied owner fact {fact.id}")

        if commit:
            db.session.commit()
        return True

    except Exception as e:
        logger.error(f"Error applying fact {fact.id}: {e}", exc_info=True)
        if commit:
            db.session.rollback()
        return False


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


def _resolve_element_id(
    organization_id: int,
    system: str,
    external_id: str,
    path: str,
) -> Optional[int]:
    """Resolve an element fact path to its applied element ID.

    Args:
        organization_id: The organization
        system: The external system
        external_id: The external item ID
        path: The fact path (e.g., "element", "team")

    Returns:
        The applied element ID, or None if not found
    """
    fact = IntegrationFact.query.filter_by(
        organization_id=organization_id,
        system=system,
        external_id=external_id,
        path=path,
        fact_type="element",
        status=IntegrationFactStatus.APPLIED,
    ).first()
    return fact.applied_element_id if fact else None
