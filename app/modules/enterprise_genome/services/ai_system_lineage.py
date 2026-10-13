"""Lineage queries for the AI Systems Register.

Reads ArchiMateRelationship rows that link an AI system to its providers and
data entities. No new table: the relationship store is the single source of
truth for every element link in the architecture.

Reuse
-----
* ArchiMateRelationship (``app.models.models``) for all element links.
* ``ApplicationOwner.get_owners_for_element`` for the owner read.
* ``profile_from_element`` for projecting elements into a typed dict.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def get_providers_for_system(element_id: int, organization_id: int) -> list[dict]:
    """Providers linked to an AI system element via ArchiMateRelationship.

    Returns a list of provider-element dicts (``{id, name, type}``), or an
    empty list when none are linked. Scoped to *organization_id* so a caller
    from another org never sees the relationship.

    Direction matters: the AI system is the *source* of the Serving or Flow
    relationship that points at a provider. A consumer application is the
    source of a relationship that points AT the system, so it can never be
    read as a provider. A provider is an element registered in the provider
    register — it carries the ``provider_register`` marker; element type
    alone never marks a provider.
    """
    from app.models.models import ArchiMateElement, ArchiMateRelationship

    # The system is the source of the provider link; consumers point at the
    # system (they are the source, the system is the target).
    rels = (
        ArchiMateRelationship.query.filter(
            ArchiMateRelationship.source_id == element_id,
            ArchiMateRelationship.type.in_(("serving", "flow")),
        )
        .filter(ArchiMateRelationship.organization_id == organization_id)
        .all()
    )

    providers: list[dict] = []
    seen: set[int] = set()

    for rel in rels:
        peer_id = rel.target_id
        if peer_id in seen:
            continue
        seen.add(peer_id)

        peer = ArchiMateElement.query.filter_by(
            id=peer_id,
            organization_id=organization_id,
        ).first()
        if peer is None:
            continue
        # A provider is an element registered in the provider register: it
        # carries the provider_register marker. Element type alone (e.g. an
        # ApplicationComponent) never marks a provider — a consumer application
        # is an ApplicationComponent too.
        props = peer.custom_properties or {}
        is_provider = props.get("provider_register") is True
        if is_provider:
            providers.append({
                "id": peer.id,
                "name": peer.name or "not recorded",
                "type": peer.type or "not recorded",
            })

    return providers


def get_data_entities_for_system(element_id: int, organization_id: int) -> list[dict]:
    """Data entities linked to an AI system via ArchiMateRelationship.

    Returns a list of data-entity element dicts (``{id, name, type}``), or an
    empty list when none are linked. A data entity is a ``DataObject`` element.

    Scoped to *organization_id*.
    """
    from app.models.models import ArchiMateElement, ArchiMateRelationship

    rels = (
        ArchiMateRelationship.query.filter(
            (
                (ArchiMateRelationship.source_id == element_id)
                | (ArchiMateRelationship.target_id == element_id)
            ),
            ArchiMateRelationship.type.in_(
                ("serving", "association", "access", "flow")
            ),
        )
        .filter(ArchiMateRelationship.organization_id == organization_id)
        .all()
    )

    entities: list[dict] = []
    seen: set[int] = set()

    for rel in rels:
        peer_id = rel.target_id if rel.source_id == element_id else rel.source_id
        if peer_id in seen:
            continue
        seen.add(peer_id)

        peer = ArchiMateElement.query.filter_by(
            id=peer_id,
            organization_id=organization_id,
        ).first()
        if peer is None:
            continue
        if peer.type == "DataObject":
            entities.append({
                "id": peer.id,
                "name": peer.name or "not recorded",
                "type": peer.type,
            })

    return entities


def get_systems_using_provider(
    provider_element_id: int,
    organization_id: int,
) -> list[dict]:
    """All AI systems using a provider, with owner and data entities sent.

    Returns::
        [
            {
                "id": <element id>,
                "name": "...",
                "owner": {"user_id": ..., "user_name": ..., ...} | None,
                "data_entities": [{"id": ..., "name": ..., "type": ...}, ...],
            },
            ...
        ]

    Scoped to *organization_id*.
    """
    from app.models.application_owner import ApplicationOwner
    from app.models.models import ArchiMateElement, ArchiMateRelationship

    # Direction: an AI system uses a provider when it is the source of a
    # Serving or Flow relationship pointing AT the provider. Consumers point at
    # the AI system, never at the provider, so only relationships where the
    # provider is the target can name a system that uses it.
    rels = (
        ArchiMateRelationship.query.filter(
            ArchiMateRelationship.target_id == provider_element_id,
            ArchiMateRelationship.type.in_(("serving", "flow")),
        )
        .filter(ArchiMateRelationship.organization_id == organization_id)
        .all()
    )

    # Collect peer system elements
    systems: list[dict] = []
    seen: set[int] = set()

    for rel in rels:
        peer_id = rel.source_id
        if peer_id in seen:
            continue
        seen.add(peer_id)

        # Must be an AI-system element
        peer = ArchiMateElement.query.filter_by(
            id=peer_id,
            organization_id=organization_id,
        ).first()
        if peer is None:
            continue

        is_ai = (
            getattr(peer, "type", None) == "ApplicationComponent"
            and isinstance(getattr(peer, "custom_properties", None) or {}, dict)
            and "ai_system" in (peer.custom_properties or {})
        )
        if not is_ai:
            continue

        # Read owner
        owners = ApplicationOwner.get_owners_for_element(
            element_type="ai_system",
            element_id=peer.id,
            organization_id=organization_id,
        )
        owner = None
        if owners:
            primary = None
            for o in owners:
                if o.ownership_type == "primary":
                    primary = o
                    break
            if primary is None:
                primary = owners[0]
            owner = {
                "user_id": primary.user_id,
                "ownership_type": primary.ownership_type,
                "user_name": getattr(primary.user, "display_name", None)
                or f"{getattr(primary.user, 'first_name', '') or ''} {getattr(primary.user, 'last_name', '') or ''}".strip()
                or None,
            }

        # Read data entities linked to this system
        data_entities = get_data_entities_for_system(peer.id, organization_id)

        systems.append({
            "id": peer.id,
            "name": peer.name or "not recorded",
            "owner": owner,
            "data_entities": data_entities,
        })

    return systems